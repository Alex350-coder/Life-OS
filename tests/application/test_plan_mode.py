"""Modo `plan` (SEC-09): solo el dueño del plan global publica, y solo en Semanal/ y Diario/."""

from typing import Any

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.yaml_config import parse_agent, parse_catalog, parse_policy
from lifeos.application.permissions import TOOL_WRITE_MODES, resolve_effective
from lifeos.application.policy_vault import PermissionDeniedError, PolicyVault
from lifeos.ports.config import ConfigRejectedError, WriteGrant, WriteMode
from lifeos.ports.vault import VaultPath
from tests import config_samples as samples
from tests.application.conftest import WriteNote

pytestmark = [pytest.mark.integration, pytest.mark.security]

DAILY = "Diario/2026-11-02.md"
DAILY_TEXT = (
    "---\ntipo: diario\n---\n\n## Bloques\n- 08:00–09:00 · A [id:: t-aaaaaa]\n\n"
    "## Check-ins\n- 09:00 · A · hecho\n\n## Cierre\nTodo bien.\n"
)


def _resolve(policy: dict[str, Any] | None = None, **agents: dict[str, Any]) -> Any:
    jefe = agents.get("jefe", samples.jefe())
    coach = agents.get("coach", samples.coach())
    return resolve_effective(
        parse_policy(samples.dump(policy or samples.policy()).encode()),
        parse_catalog(samples.dump(samples.catalog()).encode()),
        (parse_agent(samples.dump(jefe).encode()), parse_agent(samples.dump(coach).encode())),
        tools=TOOL_WRITE_MODES,
        contracts=samples.KNOWN_CONTRACTS,
    )


def _path(raw: str) -> VaultPath:
    return VaultPath.parse(raw)


# --- Permisos efectivos -----------------------------------------------------------------------


def test_plan_owner_with_the_tool_gets_plan_grants_from_the_policy() -> None:
    jefe = _resolve()["jefe"]
    assert "publicar_plan" in jefe.tools
    assert jefe.plan_writes == (
        WriteGrant(VaultPath("Semanal"), WriteMode.PLAN),
        WriteGrant(VaultPath("Diario"), WriteMode.PLAN),
    )
    assert all(grant.mode is not WriteMode.PLAN for grant in jefe.writes)


def test_coach_never_gets_plan_grants() -> None:
    assert _resolve()["coach"].plan_writes == ()


def test_owner_without_the_tool_gets_no_plan_grants() -> None:
    jefe = samples.jefe()
    jefe["herramientas"] = ["leer_nota", "listar_tareas", "crear_captura"]
    assert _resolve(jefe=jefe)["jefe"].plan_writes == ()


def test_coach_requesting_the_publish_tool_is_rejected() -> None:
    coach = samples.coach()
    coach["herramientas"] = [*coach["herramientas"], "publicar_plan"]
    with pytest.raises(ConfigRejectedError, match="publicar_plan"):
        _resolve(coach=coach)


def test_policy_giving_the_publish_tool_to_another_agent_is_rejected() -> None:
    policy = samples.policy()
    policy["agentes"]["coach"]["herramientas"].append("publicar_plan")
    with pytest.raises(ConfigRejectedError, match="publicar_plan"):
        _resolve(policy)


def test_owner_tool_without_plan_grants_in_the_policy_is_rejected() -> None:
    policy = samples.policy()
    policy["agentes"]["jefe"]["escritura"] = [{"ruta": "00-Inbox/", "modo": "crear"}]
    with pytest.raises(ConfigRejectedError, match="publicar_plan"):
        _resolve(policy)


def test_agent_file_still_cannot_request_plan_mode() -> None:
    jefe = samples.jefe()
    jefe["permisos_solicitados"]["escritura"].append({"ruta": "Semanal/", "modo": "plan"})
    with pytest.raises(ConfigRejectedError, match="modo plan"):
        _resolve(jefe=jefe)


# --- Bóveda con permisos en modo plan ---------------------------------------------------------


def _jefe(vault: FilesystemVault) -> PolicyVault:
    return PolicyVault(vault, samples.effective()["jefe"], WriteMode.PLAN)


def test_owner_publishes_weekly_and_daily_notes(vault: FilesystemVault) -> None:
    jefe = _jefe(vault)
    jefe.write(_path("Semanal/2026-W45.md"), "plan\n", expected=None)
    jefe.write(_path(DAILY), DAILY_TEXT, expected=None)
    assert vault.read(_path("Semanal/2026-W45.md")).text == "plan\n"


def test_owner_replaces_a_plan_note_with_its_version(vault: FilesystemVault) -> None:
    jefe = _jefe(vault)
    version = jefe.write(_path("Semanal/2026-W45.md"), "v1\n", expected=None)
    jefe.write(_path("Semanal/2026-W45.md"), "v2\n", expected=version)
    assert vault.read(_path("Semanal/2026-W45.md")).text == "v2\n"


@pytest.mark.parametrize(
    "raw",
    ["Semanal/notas.md", "Semanal/2026-W45-copia.md", "Diario/hoy.md", "Diario/2026/2026-11-02.md"],
)
def test_plan_notes_must_use_the_canonical_names(vault: FilesystemVault, raw: str) -> None:
    with pytest.raises(PermissionDeniedError) as info:
        _jefe(vault).write(_path(raw), "x\n", expected=None)
    assert info.value.reason == "nombre_de_plan_invalido"


@pytest.mark.parametrize("raw", ["01-Universidad/2026-W45.md", "00-Inbox/2026-W45.md"])
def test_plan_mode_cannot_write_outside_plan_folders(vault: FilesystemVault, raw: str) -> None:
    with pytest.raises(PermissionDeniedError):
        _jefe(vault).write(_path(raw), "x\n", expected=None)


def test_coach_in_plan_mode_is_denied(vault: FilesystemVault) -> None:
    coach = PolicyVault(vault, samples.effective()["coach"], WriteMode.PLAN)
    with pytest.raises(PermissionDeniedError) as info:
        coach.write(_path("Semanal/2026-W45.md"), "plan\n", expected=None)
    assert info.value.reason == "escritura_no_autorizada"


def test_republishing_a_daily_note_must_keep_checkins_and_closing(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(DAILY, DAILY_TEXT)
    version = vault.read(_path(DAILY)).version
    jefe = _jefe(vault)
    altered = DAILY_TEXT.replace("- 09:00 · A · hecho", "- 09:00 · A · borrado")
    with pytest.raises(PermissionDeniedError) as info:
        jefe.write(_path(DAILY), altered, expected=version)
    assert info.value.reason == "seccion_protegida_alterada"
    without_closing = DAILY_TEXT.replace("## Cierre\nTodo bien.\n", "")
    with pytest.raises(PermissionDeniedError):
        jefe.write(_path(DAILY), without_closing, expected=version)
    new_plan = DAILY_TEXT.replace("08:00–09:00 · A", "10:00–11:00 · A")
    jefe.write(_path(DAILY), new_plan, expected=version)
    assert vault.read(_path(DAILY)).text == new_plan
