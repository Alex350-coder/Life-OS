"""Validación de especialistas contra la política (F8-T01/T02, ADR-0004, RULES C.3, E.10-E.12)."""

from typing import Any

import pytest

from lifeos.adapters.yaml_config import parse_agent, parse_catalog, parse_policy
from lifeos.application.permissions import (
    PROPOSE_TOOL,
    TOOL_WRITE_MODES,
    validate_specialist,
)
from lifeos.domain.task import Area
from lifeos.ports.config import ConfigRejectedError, ConfigSyntaxError, WriteMode
from tests.config_samples import (
    KNOWN_CONTRACTS,
    catalog,
    dump,
    policy_with_specialist,
    specialist,
)

pytestmark = pytest.mark.security

CORE = frozenset({"jefe", "coach"})


def _validate(agent: dict[str, Any], policy: dict[str, Any] | None = None) -> object:
    return validate_specialist(
        parse_agent(dump(agent).encode()),
        parse_policy(dump(policy or policy_with_specialist()).encode()),
        parse_catalog(dump(catalog()).encode()),
        tools=TOOL_WRITE_MODES,
        contracts=KNOWN_CONTRACTS,
        core_ids=CORE,
    )


def _problems(agent: dict[str, Any], policy: dict[str, Any] | None = None) -> str:
    with pytest.raises(ConfigRejectedError) as raised:
        _validate(agent, policy)
    return " | ".join(raised.value.violations)


def test_valid_specialist_gets_effective_permissions() -> None:
    perms = validate_specialist(
        parse_agent(dump(specialist()).encode()),
        parse_policy(dump(policy_with_specialist()).encode()),
        parse_catalog(dump(catalog()).encode()),
        tools=TOOL_WRITE_MODES,
        contracts=KNOWN_CONTRACTS,
        core_ids=CORE,
    )

    assert perms.agent_id == "universidad"
    assert perms.tools == frozenset({"leer_nota", "listar_tareas", PROPOSE_TOOL})
    assert perms.plan_writes == ()
    assert [str(w.root) for w in perms.writes] == ["Propuestas"]


def test_propose_tool_needs_create_mode() -> None:
    assert TOOL_WRITE_MODES[PROPOSE_TOOL] is WriteMode.CREAR


def test_area_is_parsed_from_file() -> None:
    assert parse_agent(dump(specialist()).encode()).area is Area.UNIVERSIDAD


def test_specialist_without_area_is_rejected() -> None:
    agent = specialist()
    del agent["area"]

    assert "área" in _problems(agent)


@pytest.mark.parametrize("area", ["salud", "finanzas"])
def test_sensitive_areas_are_rejected(area: str) -> None:
    assert "área" in _problems(specialist(area=area))


def test_unknown_area_is_a_syntax_error() -> None:
    with pytest.raises(ConfigSyntaxError, match="area"):
        parse_agent(dump(specialist(area="astrologia")).encode())


@pytest.mark.parametrize("core", ["jefe", "coach"])
def test_core_ids_cannot_be_imported(core: str) -> None:
    policy = policy_with_specialist()
    policy["agentes"][core]["herramientas"].append("proponer_al_jefe")

    assert "núcleo" in _problems(specialist(agent_id=core), policy)


def test_plan_owner_id_cannot_be_imported() -> None:
    policy = policy_with_specialist()
    policy["global"]["dueno_plan_global"] = "universidad"

    problems = _problems(specialist(), policy)

    assert "dueño del plan" in problems


def test_specialist_cannot_write_outside_proposals() -> None:
    policy = policy_with_specialist()
    policy["agentes"]["universidad"]["escritura"].append({"ruta": "Semanal/", "modo": "crear"})
    agent = specialist()
    agent["permisos_solicitados"]["escritura"].append({"ruta": "Semanal/", "modo": "crear"})

    assert "Propuestas/" in _problems(agent, policy)


@pytest.mark.parametrize("tool", ["crear_captura", "anexar_checkin", "publicar_calendario"])
def test_specialist_tools_are_limited_even_if_policy_allows(tool: str) -> None:
    policy = policy_with_specialist()
    policy["agentes"]["universidad"]["herramientas"].append(tool)
    policy["agentes"]["universidad"]["escritura"].append({"ruta": "00-Inbox/", "modo": "crear"})
    agent = specialist()
    agent["herramientas"].append(tool)

    assert "especialista" in _problems(agent, policy)


def test_specialist_cannot_request_publish_plan() -> None:
    agent = specialist()
    agent["herramientas"].append("publicar_plan")

    problems = _problems(agent)

    assert "publicar_plan" in problems


def test_specialist_must_read_its_area() -> None:
    agent = specialist()
    agent["permisos_solicitados"]["lectura"] = []

    assert "01-Universidad/" in _problems(agent)


def test_excess_reads_are_rejected_not_trimmed() -> None:
    agent = specialist()
    agent["permisos_solicitados"]["lectura"].append("02-Proyectos/")

    assert "excede la política" in _problems(agent)


def test_restricted_reads_are_rejected() -> None:
    agent = specialist()
    agent["permisos_solicitados"]["lectura"].append("05-Salud/")

    assert "restringida" in _problems(agent)


def test_specialist_without_policy_entry_is_rejected_with_hint() -> None:
    problems = _problems(specialist(agent_id="proyectos", area="proyectos"))

    assert "no tiene entrada en la política" in problems


def test_unknown_contract_is_rejected() -> None:
    agent = specialist()
    agent["contrato"]["salida"] = "plan_secreto_v9"

    assert "contrato" in _problems(agent)


def test_unknown_profile_is_rejected() -> None:
    agent = specialist()
    agent["modelo_solicitado"]["perfil"] = "gigante"

    assert "perfil" in _problems(agent)


def test_all_problems_are_reported_together() -> None:
    agent = specialist()
    agent["modelo_solicitado"]["perfil"] = "gigante"
    agent["permisos_solicitados"]["lectura"].append("02-Proyectos/")

    problems = _problems(agent)

    assert "perfil" in problems
    assert "excede" in problems


def test_invalid_policy_also_rejects_specialist() -> None:
    policy = policy_with_specialist()
    policy["global"]["escritura_prohibida_para_agentes"] = ["_logs/"]

    assert "RULES E.12" in _problems(specialist(), policy)
