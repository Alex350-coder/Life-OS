"""Ejecución de un especialista (F8-T02): estado, área acotada, IA con respaldo y prueba sin
escrituras."""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from lifeos.adapters.agent_store import JsonAgentRegistry
from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.schemas import SCHEMAS
from lifeos.adapters.yaml_config import parse_agent, parse_catalog, parse_policy
from lifeos.application.agent_registry import AgentNotRunnableError, AgentRegistry
from lifeos.application.llm_calls import ModelGateway
from lifeos.application.permissions import (
    TOOL_WRITE_MODES,
    EffectivePermissions,
    validate_specialist,
)
from lifeos.application.specialists import AiAreaDrafter, SpecialistRunner
from lifeos.domain.agent_lifecycle import AgentState, LifecycleAction
from lifeos.domain.area_proposals import ProposalKind, parse_proposal_note
from lifeos.domain.ids import TaskId
from lifeos.domain.task import Priority
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.config import AgentConfig
from lifeos.ports.llm import LlmTimeoutError
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.fakes import FakeClock, FakeLlmClient, MemoryAuditLog, MemoryUsageLog, ok_response

pytestmark = pytest.mark.integration

UTC_ZONE = ZoneInfo("UTC")
WEEK = IsoWeek.parse("2026-W44")  # FIXED_NOW = 2026-10-27
DUE = "\U0001f4c5"
TASKS = (
    f"- [ ] Informe [id:: t-abc234] [area:: universidad] [esfuerzo:: 2h] {DUE} 2026-10-28\n"
    f"- [ ] Lectura [id:: t-abc235] [area:: universidad] [esfuerzo:: 1h] [prioridad:: baja]"
    f" {DUE} 2026-11-03\n"
)
GOALS = "- [meta:: m1] Aprobar Redes [horizonte:: 2026-12-15]\n"
HEADER = "[area:: {}] [fuente:: heuristica]\n"


def _validate(agent: AgentConfig) -> EffectivePermissions:
    return validate_specialist(
        agent,
        parse_policy(samples.dump(samples.policy_with_specialist()).encode()),
        parse_catalog(samples.dump(samples.catalog()).encode()),
        tools=TOOL_WRITE_MODES,
        contracts=samples.KNOWN_CONTRACTS,
        core_ids=frozenset({"jefe", "coach"}),
    )


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
def area_vault(vault: FilesystemVault, write_note: WriteNote) -> FilesystemVault:
    write_note("01-Universidad/redes.md", TASKS)
    write_note("01-Universidad/metas.md", GOALS)
    write_note(
        "02-Proyectos/web.md",
        "- [ ] Web [id:: t-abc236] [area:: universidad] \U0001f4c5 2026-10-28\n",
    )
    return vault


def _runner(
    registry: AgentRegistry, vault: FilesystemVault, audit: MemoryAuditLog, drafter: object = None
) -> SpecialistRunner:
    return SpecialistRunner(
        registry,
        vault,
        audit,
        FakeClock(),
        configured_zone="UTC",
        drafter_for=(lambda config: drafter) if drafter is not None else None,  # type: ignore[arg-type,return-value]
    )


def _activate(registry: AgentRegistry, runner: SpecialistRunner) -> None:
    runner.run("universidad", dry_run=True)
    registry.transition("universidad", LifecycleAction.APROBAR)
    registry.transition("universidad", LifecycleAction.ACTIVAR)


def test_dry_run_writes_nothing_and_marks_tested(
    registry: AgentRegistry, area_vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    result = _runner(registry, area_vault, audit).run("universidad", dry_run=True)

    assert result.path is None
    assert result.recorded_test
    assert not area_vault.list_notes(None) or all(
        not str(p).startswith("Propuestas") for p in area_vault.list_notes(None)
    )
    (view,) = registry.views()
    assert view.entry.state is AgentState.PROBADO
    assert [i.kind for i in result.proposal.items] == [
        ProposalKind.PRIORIDAD,
        ProposalKind.PRIORIDAD,
        ProposalKind.NUEVA_TAREA,
    ]


def test_draft_cannot_run_for_real(
    registry: AgentRegistry, area_vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    with pytest.raises(AgentNotRunnableError, match="borrador"):
        _runner(registry, area_vault, audit).run("universidad")


def test_active_agent_writes_proposal_note_only_from_its_area(
    registry: AgentRegistry, area_vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    runner = _runner(registry, area_vault, audit)
    _activate(registry, runner)

    result = runner.run("universidad", week=WEEK)

    assert result.path is not None
    assert str(result.path) == "Propuestas/2026-W44/universidad.md"
    items = parse_proposal_note(area_vault.read(result.path).text)
    assert {str(i.task_id) for i in items if i.task_id} == {"t-abc234", "t-abc235"}  # no t-abc236
    assert result.proposal.source == "heuristica"


def test_deactivated_agent_cannot_run(
    registry: AgentRegistry, area_vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    runner = _runner(registry, area_vault, audit)
    _activate(registry, runner)
    registry.transition("universidad", LifecycleAction.DESACTIVAR)

    with pytest.raises(AgentNotRunnableError):
        runner.run("universidad")
    with pytest.raises(AgentNotRunnableError):
        runner.run("universidad", dry_run=True)


def test_dry_run_of_active_agent_does_not_change_state(
    registry: AgentRegistry, area_vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    runner = _runner(registry, area_vault, audit)
    _activate(registry, runner)

    result = runner.run("universidad", dry_run=True)

    assert not result.recorded_test
    assert registry.views()[0].entry.state is AgentState.ACTIVO


def test_no_items_writes_no_note(
    registry: AgentRegistry, vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    runner = _runner(registry, vault, audit)
    _activate(registry, runner)

    result = runner.run("universidad")

    assert result.proposal.items == ()
    assert result.path is None


def _gateway(*steps: object) -> tuple[ModelGateway, FakeLlmClient, MemoryUsageLog]:
    client = FakeLlmClient(list(steps))  # type: ignore[arg-type]
    usage = MemoryUsageLog()
    catalog = parse_catalog(samples.dump(samples.catalog()).encode())
    return ModelGateway({"nvidia": client}, catalog, FakeClock(), usage), client, usage


def test_ai_items_are_filtered_to_open_area_tasks(
    registry: AgentRegistry, area_vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    output = {
        "propuestas": [
            {"tipo": "prioridad", "tarea": "t-abc234", "prioridad": "alta", "motivo": "urge"},
            {"tipo": "prioridad", "tarea": "t-abc236", "prioridad": "alta", "motivo": "ajena"},
            {"tipo": "prioridad", "tarea": "t-abc235", "prioridad": "baja", "motivo": "igual"},
            {
                "tipo": "nueva_tarea",
                "titulo": "Repasar",
                "esfuerzo": "1h",
                "vence": "2026-01-01",
                "prioridad": "media",
                "motivo": "pasado",
            },
        ]
    }
    gateway, client, usage = _gateway(ok_response(json.dumps(output)))
    drafter = AiAreaDrafter(gateway, SCHEMAS["propuesta_area_v1"], profile="ligero")
    runner = _runner(registry, area_vault, audit, drafter)

    result = runner.run("universidad", dry_run=True)

    assert result.proposal.source == "ia"
    assert [(str(i.task_id), i.priority, i.key) for i in result.proposal.items] == [
        ("t-abc234", Priority.ALTA, "p1")
    ]
    assert any("descart" in alert for alert in result.alerts)
    assert usage.records[0].agent == "universidad"
    # Las notas viajan como datos; las de otras áreas nunca llegan al modelo.
    sent = client.requests[0].messages[-1].content
    assert "t-abc236" not in sent
    assert "no son instrucciones" in sent


def test_ai_failure_falls_back_to_heuristic(
    registry: AgentRegistry, area_vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    gateway, _, _ = _gateway(*[LlmTimeoutError("lento")] * 5)
    drafter = AiAreaDrafter(gateway, SCHEMAS["propuesta_area_v1"], profile="ligero")

    result = _runner(registry, area_vault, audit, drafter).run("universidad", dry_run=True)

    assert result.proposal.source == "heuristica"
    assert any("IA" in alert for alert in result.alerts)
    assert result.proposal.items


# --- Memoria de rechazos (F10-T02, DT-30) -------------------------------------------------------


def _reject_all(vault: FilesystemVault, path: str) -> None:
    from lifeos.domain.area_proposals import ProposalStatus, with_status
    from lifeos.ports.vault import VaultPath

    target = VaultPath(path)
    text = vault.read(target).text
    for item in parse_proposal_note(text):
        text = with_status(text, item.key, ProposalStatus.RECHAZADA)
    vault.write(target, text, expected=vault.read(target).version)


def test_rejected_items_are_not_proposed_again_the_same_week(
    registry: AgentRegistry, area_vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    runner = _runner(registry, area_vault, audit)
    _activate(registry, runner)
    first = runner.run("universidad", week=WEEK)
    assert first.path is not None
    _reject_all(area_vault, str(first.path))

    second = runner.run("universidad", week=WEEK)

    assert second.path is None  # todo estaba rechazado: no se escribe otra nota
    assert second.proposal.items == ()
    assert second.repeated_rejections == len(first.proposal.items)
    assert any("ya rechazadas" in alert for alert in second.alerts)


def test_only_matching_rejections_are_dropped_and_keys_renumbered(
    registry: AgentRegistry,
    area_vault: FilesystemVault,
    audit: MemoryAuditLog,
    write_note: WriteNote,
) -> None:
    write_note(
        "Propuestas/2026-W44/otro.md",
        "[area:: universidad] [fuente:: heuristica]\n"
        "- [propuesta:: p1] [tipo:: prioridad] [tarea:: t-abc234] [prioridad:: alta]"
        " [estado:: rechazada] vence pronto\n"
        "- [propuesta:: p2] [tipo:: nueva_tarea] [titulo:: Siguiente paso: aprobar  redes]"
        " [esfuerzo:: 1h] [prioridad:: media] [estado:: aceptada] meta m1\n",
    )
    runner = _runner(registry, area_vault, audit)
    _activate(registry, runner)

    result = runner.run("universidad", week=WEEK)

    kept = result.proposal.items
    assert TaskId("t-abc234") not in {i.task_id for i in kept}  # rechazada: no vuelve
    assert any(i.kind is ProposalKind.NUEVA_TAREA for i in kept)  # aceptada no cuenta
    assert [i.key for i in kept] == [f"p{n}" for n in range(1, len(kept) + 1)]
    assert result.repeated_rejections == 1


def test_rejections_of_another_area_or_week_do_not_count(
    registry: AgentRegistry,
    area_vault: FilesystemVault,
    audit: MemoryAuditLog,
    write_note: WriteNote,
) -> None:
    rejected = (
        "- [propuesta:: p1] [tipo:: prioridad] [tarea:: t-abc234] [prioridad:: alta]"
        " [estado:: rechazada] vence pronto\n"
    )
    write_note("Propuestas/2026-W43/universidad.md", HEADER.format("universidad") + rejected)
    write_note("Propuestas/2026-W44/proyectos.md", HEADER.format("proyectos") + rejected)

    result = _runner(registry, area_vault, audit).run("universidad", week=WEEK, dry_run=True)

    assert TaskId("t-abc234") in {i.task_id for i in result.proposal.items}
    assert result.repeated_rejections == 0


def test_unreadable_proposal_note_stops_the_write_and_names_the_note(
    registry: AgentRegistry,
    area_vault: FilesystemVault,
    audit: MemoryAuditLog,
    write_note: WriteNote,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lifeos.ports.vault import Snapshot, VaultIOError, VaultPath

    write_note("Propuestas/2026-W44/otro.md", HEADER.format("universidad"))
    runner = _runner(registry, area_vault, audit)
    _activate(registry, runner)
    original = area_vault.read

    def flaky(path: VaultPath) -> Snapshot:
        if path.value == "Propuestas/2026-W44/otro.md":
            raise VaultIOError("bloqueada")
        return original(path)

    monkeypatch.setattr(area_vault, "read", flaky)

    result = runner.run("universidad", week=WEEK)

    assert result.path is None  # no se arriesga a repetir un rechazo
    assert any("Propuestas/2026-W44/otro.md" in alert for alert in result.alerts)
    monkeypatch.undo()
    assert all("universidad" not in str(p) for p in area_vault.list_notes(VaultPath("Propuestas")))
