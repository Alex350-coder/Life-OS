"""SEC-05: permisos efectivos = solicitados ∩ política; un exceso rechaza la configuración."""

from collections.abc import Callable
from typing import Any

import pytest
from lifeos.adapters.yaml_config import parse_agent, parse_catalog, parse_policy
from lifeos.application.permissions import (
    TOOL_WRITE_MODES,
    protected_roots,
    resolve_effective,
)
from lifeos.ports.config import ConfigRejectedError, WriteGrant, WriteMode

from lifeos.ports.vault import VaultPath
from tests import config_samples as samples

pytestmark = [pytest.mark.unit, pytest.mark.security]

Data = dict[str, Any]


def _resolve(
    policy: Data | None = None,
    agents: list[Data] | None = None,
    catalog: Data | None = None,
) -> Any:
    return resolve_effective(
        parse_policy(samples.dump(policy or samples.policy()).encode()),
        parse_catalog(samples.dump(catalog or samples.catalog()).encode()),
        tuple(
            parse_agent(samples.dump(a).encode())
            for a in (agents if agents is not None else [samples.jefe(), samples.coach()])
        ),
        tools=TOOL_WRITE_MODES,
        contracts=samples.KNOWN_CONTRACTS,
    )


def _rejected(**kwargs: Any) -> tuple[str, ...]:
    with pytest.raises(ConfigRejectedError) as info:
        _resolve(**kwargs)
    return info.value.violations


def _coach_with(mutate: Callable[[Data], None]) -> list[Data]:
    coach = samples.coach()
    mutate(coach)
    return [samples.jefe(), coach]


def _write(ruta: str, modo: str, seccion: str | None = None) -> Data:
    entry: Data = {"ruta": ruta, "modo": modo}
    if seccion is not None:
        entry["seccion"] = seccion
    return entry


# --- Caso válido --------------------------------------------------------------------------


def test_valid_config_yields_effective_permissions_equal_to_request() -> None:
    effective = _resolve()
    coach = effective["coach"]
    assert coach.reads == (VaultPath("_sistema"), VaultPath("Semanal"), VaultPath("Diario"))
    assert coach.writes == (
        WriteGrant(VaultPath("00-Inbox"), WriteMode.CREAR),
        WriteGrant(VaultPath("Diario"), WriteMode.ANEXAR_SECCION, "check-ins"),
    )
    assert coach.tools == frozenset(TOOL_WRITE_MODES)
    assert coach.profile == "ligero"
    assert effective["jefe"].writes == (WriteGrant(VaultPath("00-Inbox"), WriteMode.CREAR),)


def test_narrower_request_inside_ceiling_is_accepted() -> None:
    def narrow(coach: Data) -> None:
        coach["permisos_solicitados"]["lectura"] = ["Diario/2026/"]

    assert _resolve(agents=_coach_with(narrow))["coach"].reads == (VaultPath("Diario/2026"),)


def test_jefe_never_gets_plan_mode_from_its_own_file() -> None:
    assert all(g.mode is not WriteMode.PLAN for g in _resolve()["jefe"].writes)


# --- Solicitudes que exceden la política (SEC-05) -----------------------------------------


@pytest.mark.parametrize(
    "write",
    [
        _write("_sistema/", "crear"),
        _write("_SISTEMA/", "crear"),
        _write("_sistema/perfil/", "crear"),
        _write("_logs/", "crear"),
        _write("Archivo/", "crear"),
        _write("Semanal/", "anexar_seccion", "check-ins"),
        _write("Semanal/", "crear"),
        _write("Diario/", "crear"),
        _write("Diario/", "anexar_seccion", "plan"),
        _write("01-Universidad/", "crear"),
    ],
)
def test_coach_write_beyond_policy_is_rejected(write: Data) -> None:
    violations = _rejected(
        agents=_coach_with(lambda c: c["permisos_solicitados"]["escritura"].append(write))
    )
    assert any("coach" in v and write["ruta"].rstrip("/") in v for v in violations)


@pytest.mark.parametrize(
    "ruta", ["05-Salud/", "05-salud/notas/", "06-Finanzas/", "01-Universidad/"]
)
def test_coach_read_beyond_policy_or_restricted_is_rejected(ruta: str) -> None:
    violations = _rejected(
        agents=_coach_with(lambda c: c["permisos_solicitados"]["lectura"].append(ruta))
    )
    assert any("lectura" in v for v in violations)


def test_unknown_or_ungranted_tools_are_rejected() -> None:
    violations = _rejected(
        agents=[
            {**samples.jefe(), "herramientas": [*samples.jefe()["herramientas"], "anexar_checkin"]},
            samples.coach(),
        ]
    )
    assert any("jefe" in v and "anexar_checkin" in v for v in violations)


def test_write_tool_without_matching_write_grant_is_rejected() -> None:
    def drop_append(coach: Data) -> None:
        coach["permisos_solicitados"]["escritura"] = [_write("00-Inbox/", "crear")]

    violations = _rejected(agents=_coach_with(drop_append))
    assert any("anexar_checkin" in v for v in violations)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda c: c["modelo_solicitado"].update(perfil="enorme"), "perfil"),
        (lambda c: c["contrato"].update(salida="plan_v9"), "contrato"),
        (lambda c: c.update(id="especialista"), "especialista"),
    ],
)
def test_unknown_profile_contract_or_agent_is_rejected(
    mutate: Callable[[Data], None], fragment: str
) -> None:
    violations = _rejected(agents=_coach_with(mutate))
    assert any(fragment in v for v in violations)


def test_duplicate_agent_ids_are_rejected() -> None:
    assert any("duplicad" in v for v in _rejected(agents=[samples.coach(), samples.coach()]))


def test_all_violations_are_reported_together() -> None:
    def many(coach: Data) -> None:
        coach["permisos_solicitados"]["lectura"].append("05-Salud/")
        coach["permisos_solicitados"]["escritura"].append(_write("_sistema/", "crear"))
        coach["modelo_solicitado"]["perfil"] = "enorme"

    assert len(_rejected(agents=_coach_with(many))) >= 3


# --- Validación de la propia política -----------------------------------------------------


@pytest.mark.parametrize("missing", ["_sistema/", "_logs/", "Archivo/"])
def test_policy_cannot_drop_the_rules_floor(missing: str) -> None:
    policy = samples.policy()
    policy["global"]["escritura_prohibida_para_agentes"].remove(missing)
    assert any(missing.rstrip("/") in v for v in _rejected(policy=policy))


def test_policy_cannot_grant_plan_mode_to_anyone_but_the_owner() -> None:
    policy = samples.policy()
    policy["agentes"]["coach"]["escritura"].append(_write("Semanal/", "plan"))
    assert any("plan" in v and "coach" in v for v in _rejected(policy=policy))


def test_policy_owner_must_be_a_declared_agent() -> None:
    policy = samples.policy()
    policy["global"]["dueno_plan_global"] = "otro"
    assert any("dueno_plan_global" in v for v in _rejected(policy=policy))


@pytest.mark.parametrize(
    ("kind", "entry"),
    [
        ("escritura", _write("_logs/", "crear")),
        ("escritura", _write("_Sistema/sub/", "crear")),
        ("lectura", "06-Finanzas/"),
    ],
)
def test_policy_grants_inside_forbidden_or_restricted_roots_are_rejected(
    kind: str, entry: object
) -> None:
    policy = samples.policy()
    policy["agentes"]["coach"][kind].append(entry)
    assert _rejected(policy=policy)


def test_policy_append_grant_requires_section_and_tools_must_exist() -> None:
    policy = samples.policy()
    policy["agentes"]["coach"]["escritura"].append(_write("Semanal/", "anexar_seccion"))
    policy["agentes"]["coach"]["herramientas"].append("borrar_todo")
    violations = _rejected(policy=policy)
    assert any("seccion" in v for v in violations)
    assert any("borrar_todo" in v for v in violations)


# --- Raíces protegidas derivadas de la política ------------------------------------------


def test_protected_roots_extend_but_never_drop_the_floor() -> None:
    policy = samples.policy()
    policy["global"]["escritura_prohibida_para_agentes"].append("Privado/")
    roots = protected_roots(parse_policy(samples.dump(policy).encode()))
    names = {root.value for root in roots}
    assert {"_sistema", "_logs", "Archivo", "00-Inbox", "Privado"} <= names
