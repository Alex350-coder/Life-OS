"""Caso de uso del registro de agentes (F8-T01, spec registro-de-agentes, RULES E.10)."""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from lifeos.adapters.agent_store import JsonAgentRegistry
from lifeos.adapters.yaml_config import parse_agent, parse_catalog, parse_policy
from lifeos.application.agent_registry import (
    OWNER_ACTOR,
    AgentNotFoundError,
    AgentNotRunnableError,
    AgentRegistry,
)
from lifeos.application.permissions import (
    TOOL_WRITE_MODES,
    EffectivePermissions,
    validate_specialist,
)
from lifeos.domain.agent_lifecycle import AgentState, LifecycleAction, LifecycleError
from lifeos.ports.config import AgentConfig, ConfigRejectedError, ConfigSyntaxError
from lifeos.ports.logs import AuditResult, LogWriteError
from lifeos.ports.state import StateError
from tests.config_samples import KNOWN_CONTRACTS, catalog, dump, policy_with_specialist, specialist
from tests.fakes import FakeClock, MemoryAuditLog

pytestmark = pytest.mark.integration


class _Policy:
    """Política que el propietario puede cambiar entre llamadas (se relee en cada validación)."""

    def __init__(self) -> None:
        self.data = policy_with_specialist()

    def validate(self, agent: AgentConfig) -> EffectivePermissions:
        return validate_specialist(
            agent,
            parse_policy(dump(self.data).encode()),
            parse_catalog(dump(catalog()).encode()),
            tools=TOOL_WRITE_MODES,
            contracts=KNOWN_CONTRACTS,
            core_ids=frozenset({"jefe", "coach"}),
        )


@pytest.fixture
def policy() -> _Policy:
    return _Policy()


@pytest.fixture
def audit() -> MemoryAuditLog:
    return MemoryAuditLog()


@pytest.fixture
def registry(tmp_path: Path, policy: _Policy, audit: MemoryAuditLog) -> AgentRegistry:
    return AgentRegistry(
        JsonAgentRegistry(tmp_path), parse_agent, policy.validate, audit, FakeClock()
    )


def _yaml(**changes: Any) -> bytes:
    data = specialist()
    data.update(changes)
    return dump(data).encode()


def _activate(registry: AgentRegistry) -> None:
    registry.import_config(_yaml(), "universidad.yaml")
    registry.record_test("universidad")
    registry.transition("universidad", LifecycleAction.APROBAR)
    registry.transition("universidad", LifecycleAction.ACTIVAR)


def test_import_registers_draft_without_activating(registry: AgentRegistry) -> None:
    entry = registry.import_config(_yaml(), "universidad.yaml")

    assert entry.state is AgentState.BORRADOR
    assert entry.current_version.sha256 == hashlib.sha256(_yaml()).hexdigest()
    with pytest.raises(AgentNotRunnableError, match="borrador"):
        registry.runnable("universidad")


def test_import_is_audited_with_hash_not_content(
    registry: AgentRegistry, audit: MemoryAuditLog
) -> None:
    registry.import_config(_yaml(), "universidad.yaml")

    assert [r.result for r in audit.records] == [AuditResult.INTENTO, AuditResult.OK]
    record = audit.records[-1]
    assert record.actor == OWNER_ACTOR
    assert record.action == "agente_importar"
    assert record.target == "agentes/universidad"
    assert record.result is AuditResult.OK
    assert record.reason is not None and record.reason.startswith("v1 sha256:")
    assert "Proponer" not in record.reason


def test_malformed_config_is_rejected_and_audited(
    registry: AgentRegistry, audit: MemoryAuditLog
) -> None:
    with pytest.raises(ConfigSyntaxError):
        registry.import_config(b"id: [roto", "x.yaml")

    assert audit.records[-1].result is AuditResult.RECHAZADO
    assert registry.views() == ()


def test_excessive_permissions_are_rejected_with_useful_error(registry: AgentRegistry) -> None:
    data = specialist()
    data["permisos_solicitados"]["lectura"].append("02-Proyectos/")

    with pytest.raises(ConfigRejectedError, match="excede la política"):
        registry.import_config(dump(data).encode(), "universidad.yaml")
    assert registry.views() == ()


def test_full_lifecycle_makes_agent_runnable(registry: AgentRegistry) -> None:
    _activate(registry)

    config, perms = registry.runnable("universidad")

    assert config.id == "universidad"
    assert perms.agent_id == "universidad"


def test_probar_cannot_be_forced_without_a_test_run(registry: AgentRegistry) -> None:
    registry.import_config(_yaml(), "universidad.yaml")

    with pytest.raises(LifecycleError, match="prueba"):
        registry.transition("universidad", LifecycleAction.PROBAR)


def test_deactivate_prevents_new_runs_and_tests(registry: AgentRegistry) -> None:
    _activate(registry)

    registry.transition("universidad", LifecycleAction.DESACTIVAR)

    with pytest.raises(AgentNotRunnableError, match="desactivado"):
        registry.runnable("universidad")
    with pytest.raises(AgentNotRunnableError, match="desactivado"):
        registry.testable("universidad")


def test_restore_recovers_previous_version(registry: AgentRegistry) -> None:
    _activate(registry)
    registry.import_config(_yaml(version=2, proposito="Otra versión"), "universidad.yaml")

    registry.transition("universidad", LifecycleAction.RESTAURAR, version=1)
    registry.transition("universidad", LifecycleAction.ACTIVAR)

    config, _ = registry.runnable("universidad")
    assert config.version == 1
    assert config.purpose == "Proponer prioridades del área al Jefe."
    assert registry.export("universidad") == _yaml()


def test_export_specific_version(registry: AgentRegistry) -> None:
    registry.import_config(_yaml(), "u.yaml")
    registry.import_config(_yaml(version=2), "u.yaml")

    assert registry.export("universidad", version=1) == _yaml()
    assert registry.export("universidad") == _yaml(version=2)


def test_tampered_version_file_is_refused(registry: AgentRegistry, tmp_path: Path) -> None:
    _activate(registry)
    (tmp_path / "universidad" / "v1.yaml").write_bytes(_yaml(proposito="manipulado"))

    with pytest.raises(StateError, match="huella"):
        registry.runnable("universidad")
    with pytest.raises(StateError, match="huella"):
        registry.export("universidad")


def test_policy_shrink_blocks_running_an_active_agent(
    registry: AgentRegistry, policy: _Policy
) -> None:
    _activate(registry)
    del policy.data["agentes"]["universidad"]

    with pytest.raises(ConfigRejectedError, match="política"):
        registry.runnable("universidad")
    (view,) = registry.views()
    assert view.problems
    assert view.effective is None


def test_activation_revalidates_against_current_policy(
    registry: AgentRegistry, policy: _Policy
) -> None:
    registry.import_config(_yaml(), "u.yaml")
    registry.record_test("universidad")
    registry.transition("universidad", LifecycleAction.APROBAR)
    policy.data["agentes"]["universidad"]["lectura"] = ["02-Proyectos/"]

    with pytest.raises(ConfigRejectedError):
        registry.transition("universidad", LifecycleAction.ACTIVAR)
    (view,) = registry.views()
    assert view.entry.state is AgentState.APROBADO


def test_unknown_agent(registry: AgentRegistry) -> None:
    with pytest.raises(AgentNotFoundError, match="nadie"):
        registry.transition("nadie", LifecycleAction.ACTIVAR)
    with pytest.raises(AgentNotFoundError):
        registry.runnable("nadie")


def test_invalid_transition_is_audited_as_rejected(
    registry: AgentRegistry, audit: MemoryAuditLog
) -> None:
    registry.import_config(_yaml(), "u.yaml")

    with pytest.raises(LifecycleError):
        registry.transition("universidad", LifecycleAction.ACTIVAR)

    last = audit.records[-1]
    assert (last.action, last.result) == ("agente_activar", AuditResult.RECHAZADO)


def test_views_show_state_versions_and_permissions(registry: AgentRegistry) -> None:
    _activate(registry)

    (view,) = registry.views()

    assert view.entry.state is AgentState.ACTIVO
    assert view.config is not None and view.config.area is not None
    assert view.effective is not None
    assert view.problems == ()


def test_no_state_change_if_audit_fails(tmp_path: Path, policy: _Policy) -> None:
    audit = MemoryAuditLog(fail_on=AuditResult.INTENTO)
    registry = AgentRegistry(
        JsonAgentRegistry(tmp_path), parse_agent, policy.validate, audit, FakeClock()
    )

    with pytest.raises(LogWriteError):
        registry.import_config(_yaml(), "u.yaml")

    assert registry.views() == ()


def test_new_version_must_bump_version_field(registry: AgentRegistry) -> None:
    registry.import_config(_yaml(), "u.yaml")

    with pytest.raises(LifecycleError, match="sube 'version'"):
        registry.import_config(_yaml(proposito="cambio sin subir versión"), "u.yaml")


def test_oversized_import_is_rejected(registry: AgentRegistry) -> None:
    with pytest.raises(ConfigSyntaxError, match="tamaño"):
        registry.import_config(b"#" * (65 * 1024), "grande.yaml")
