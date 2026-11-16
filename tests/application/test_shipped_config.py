"""La configuración versionada en `project/config/` es válida y respeta la política (ADR-0004)."""

from pathlib import Path

import pytest

from lifeos.adapters.yaml_config import YamlConfigSource, parse_agent
from lifeos.application.permissions import TOOL_WRITE_MODES, resolve_effective, validate_specialist
from lifeos.ports.config import WriteMode
from lifeos.ports.vault import VaultPath, within_any
from tests.config_samples import KNOWN_CONTRACTS

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.fixture(scope="module")
def source() -> YamlConfigSource:
    return YamlConfigSource(CONFIG_DIR)


def test_shipped_config_resolves_for_jefe_and_coach(source: YamlConfigSource) -> None:
    effective = resolve_effective(
        source.load_policy(),
        source.load_catalog(),
        source.load_agents(),
        tools=TOOL_WRITE_MODES,
        contracts=KNOWN_CONTRACTS,
    )
    assert set(effective) == {"jefe", "coach"}
    # Fase 3 (SEC-09): solo el Jefe recibe las concesiones de publicación del plan.
    assert {str(grant.root) for grant in effective["jefe"].plan_writes} == {"Semanal", "Diario"}
    assert effective["coach"].plan_writes == ()


def test_shipped_coach_answers_with_the_coach_contract(source: YamlConfigSource) -> None:
    coach = next(agent for agent in source.load_agents() if agent.id == "coach")
    assert coach.contract_out == "coach_v1"


def test_shipped_coach_cannot_write_plan_or_system_folders(source: YamlConfigSource) -> None:
    coach = next(agent for agent in source.load_agents() if agent.id == "coach")
    forbidden = (VaultPath("Semanal"), VaultPath("_sistema"), VaultPath("_logs"))
    assert not any(within_any(grant.root, forbidden) for grant in coach.writes)
    assert all(grant.mode is not WriteMode.PLAN for grant in coach.writes)


def test_shipped_catalog_uses_only_nvidia_and_declares_unknown_costs_honestly(
    source: YamlConfigSource,
) -> None:
    catalog = source.load_catalog()
    assert set(catalog.providers) == {"nvidia"}
    assert {profile.provider for profile in catalog.profiles.values()} == {"nvidia"}
    # Sin tarifas fechadas, el costo se registra como desconocido (nunca 0).
    assert catalog.tariff_date is not None or not catalog.tariffs


SPECIALISTS = sorted((CONFIG_DIR / "especialistas").glob("*.yaml"))


def test_four_specialist_templates_are_shipped() -> None:
    assert [path.stem for path in SPECIALISTS] == ["carrera", "hobbies", "proyectos", "universidad"]


@pytest.mark.parametrize("path", SPECIALISTS, ids=lambda path: path.stem)
def test_shipped_specialists_validate_against_shipped_policy(
    source: YamlConfigSource, path: Path
) -> None:
    agent = parse_agent(path.read_bytes(), path.name)
    perms = validate_specialist(
        agent,
        source.load_policy(),
        source.load_catalog(),
        tools=TOOL_WRITE_MODES,
        contracts=KNOWN_CONTRACTS,
        core_ids=frozenset({"jefe", "coach"}),
    )
    assert agent.id == path.stem
    assert perms.plan_writes == ()
    assert {str(grant.root) for grant in perms.writes} == {"Propuestas"}


def test_specialist_templates_are_not_loaded_as_core_agents(source: YamlConfigSource) -> None:
    assert {agent.id for agent in source.load_agents()} == {"jefe", "coach"}
