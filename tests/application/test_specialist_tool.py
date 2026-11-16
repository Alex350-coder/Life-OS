"""Herramienta proponer_al_jefe y lectura acotada del especialista (F8-T02, SEC-04, RULES C.3)."""

from collections.abc import Mapping

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.yaml_config import parse_agent, parse_catalog, parse_policy
from lifeos.application.permissions import (
    TOOL_WRITE_MODES,
    EffectivePermissions,
    validate_specialist,
)
from lifeos.application.policy_vault import PermissionDeniedError
from lifeos.application.tasks import TaskQuery, query_tasks
from lifeos.application.tools import ActorContext, ToolRegistry
from lifeos.domain.area_proposals import AreaProposal, ProposalItem, ProposalKind
from lifeos.domain.ids import TaskId
from lifeos.domain.task import Area, Priority
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.logs import AuditResult
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.fakes import FakeClock, MemoryAuditLog

pytestmark = [pytest.mark.integration, pytest.mark.security]

UNI = ActorContext(agent_id="universidad", correlation_id="c-1")
WEEK = IsoWeek.parse("2026-W46")


def _perms() -> Mapping[str, EffectivePermissions]:
    perms = validate_specialist(
        parse_agent(samples.dump(samples.specialist()).encode()),
        parse_policy(samples.dump(samples.policy_with_specialist()).encode()),
        parse_catalog(samples.dump(samples.catalog()).encode()),
        tools=TOOL_WRITE_MODES,
        contracts=samples.KNOWN_CONTRACTS,
        core_ids=frozenset({"jefe", "coach"}),
    )
    return {**samples.effective(), "universidad": perms}


def _tools(vault: FilesystemVault, audit: MemoryAuditLog | None = None) -> ToolRegistry:
    return ToolRegistry(vault, _perms(), audit or MemoryAuditLog(), FakeClock())


def _proposal(agent_id: str = "universidad") -> AreaProposal:
    item = ProposalItem(
        "p1", ProposalKind.PRIORIDAD, Priority.ALTA, "vence pronto", task_id=TaskId("t-abc234")
    )
    return AreaProposal(agent_id, Area.UNIVERSIDAD, WEEK, "heuristica", (item,))


def test_proposal_is_written_as_new_note_and_audited(vault: FilesystemVault) -> None:
    audit = MemoryAuditLog()

    path = _tools(vault, audit).proponer_al_jefe(UNI, _proposal())

    assert str(path) == "Propuestas/2026-W46/universidad.md"
    assert "[propuesta:: p1]" in vault.read(path).text
    assert [(r.action, r.result) for r in audit.records] == [
        ("proponer_al_jefe", AuditResult.INTENTO),
        ("proponer_al_jefe", AuditResult.OK),
    ]


def test_second_run_creates_a_revision_never_overwrites(vault: FilesystemVault) -> None:
    tools = _tools(vault)
    first = tools.proponer_al_jefe(UNI, _proposal())

    second = tools.proponer_al_jefe(UNI, _proposal())

    assert str(second) == "Propuestas/2026-W46/universidad-2.md"
    assert vault.exists(first)


def test_agent_cannot_propose_on_behalf_of_another(vault: FilesystemVault) -> None:
    audit = MemoryAuditLog()

    with pytest.raises(PermissionDeniedError):
        _tools(vault, audit).proponer_al_jefe(UNI, _proposal(agent_id="hobbies"))

    assert audit.records[-1].result is AuditResult.RECHAZADO


@pytest.mark.parametrize("actor", ["coach", "jefe"])
def test_core_agents_cannot_propose(vault: FilesystemVault, actor: str) -> None:
    with pytest.raises(PermissionDeniedError):
        _tools(vault).proponer_al_jefe(ActorContext(actor, "c-9"), _proposal(agent_id=actor))


def test_specialist_reads_only_its_area(vault: FilesystemVault, write_note: WriteNote) -> None:
    write_note("01-Universidad/redes.md", "- [ ] Informe [id:: t-abc234] [area:: universidad]\n")
    write_note("02-Proyectos/web.md", "- [ ] Web [id:: t-abc235] [area:: proyectos]\n")
    write_note("_sistema/perfil.md", "privado\n")
    tools = _tools(vault)

    tasks = tools.listar_tareas(UNI, TaskQuery())

    assert [str(t.task.id) for t in tasks] == ["t-abc234"]
    with pytest.raises(PermissionDeniedError):
        tools.leer_nota(UNI, "_sistema/perfil.md")


def test_specialist_cannot_publish_or_capture(vault: FilesystemVault) -> None:
    tools = _tools(vault)

    with pytest.raises(PermissionDeniedError):
        tools.crear_captura(UNI, "intento de captura")


def test_proposal_notes_are_not_scanned_as_tasks(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note("Propuestas/2026-W46/universidad.md", "- [ ] parece tarea [id:: t-abc236]\n")

    assert query_tasks(vault, TaskQuery()) == ()
