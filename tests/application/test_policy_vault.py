"""SEC-04 (núcleo): la bóveda con permisos aplica lectura, creación y anexado por agente."""

import pytest
from lifeos.application.policy_vault import PermissionDeniedError, PolicyVault

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.ports.config import WriteMode
from lifeos.ports.vault import VaultPath, VersionConflictError
from tests import config_samples as samples
from tests.application.conftest import WriteNote

pytestmark = [pytest.mark.integration, pytest.mark.security]

DAILY = "Diario/2026-10-27.md"
DAILY_TEXT = "# Martes\n## Plan\n- bloque\n\n## Check-ins\n- 08:00 — Empecé\n"


def _coach(vault: FilesystemVault, mode: WriteMode | None) -> PolicyVault:
    return PolicyVault(vault, samples.effective()["coach"], mode)


def _path(raw: str) -> VaultPath:
    return VaultPath.parse(raw)


# --- Lectura ----------------------------------------------------------------------------------


def test_reads_are_limited_to_granted_roots(vault: FilesystemVault, write_note: WriteNote) -> None:
    write_note(DAILY, DAILY_TEXT)
    write_note("01-Universidad/redes.md", "privado del jefe\n")
    write_note("05-Salud/x.md", "salud\n")
    coach = _coach(vault, None)

    assert coach.read(_path(DAILY)).text == DAILY_TEXT
    for raw in ["01-Universidad/redes.md", "05-Salud/x.md", "00-Inbox/x.md"]:
        with pytest.raises(PermissionDeniedError) as info:
            coach.read(_path(raw))
        assert info.value.reason == "lectura_no_autorizada"
    assert coach.list_notes() == (_path(DAILY),)
    assert coach.exists(_path(DAILY))


def test_read_only_mode_cannot_write(vault: FilesystemVault, write_note: WriteNote) -> None:
    write_note(DAILY, DAILY_TEXT)
    with pytest.raises(PermissionDeniedError) as info:
        _coach(vault, None).write(_path("00-Inbox/x.md"), "x\n", expected=None)
    assert info.value.reason == "solo_lectura"


# --- Matriz SEC-04 --------------------------------------------------------------------------


FORBIDDEN_TARGETS = [
    "Semanal/2026-W44.md",
    "semanal/2026-W44.md",
    "_sistema/perfil.md",
    "_SISTEMA/perfil.md",
    "_logs/x.md",
    "_Logs/x.md",
    "Archivo/Diario/x.md",
    "01-Universidad/x.md",
]


@pytest.mark.parametrize("target", FORBIDDEN_TARGETS)
@pytest.mark.parametrize("mode", [WriteMode.CREAR, WriteMode.ANEXAR_SECCION])
def test_coach_cannot_write_outside_its_grants(
    vault: FilesystemVault, target: str, mode: WriteMode
) -> None:
    coach = _coach(vault, mode)
    with pytest.raises(PermissionDeniedError):
        coach.write(_path(target), "- 09:00 — x\n", expected=None)
    assert not vault.list_notes()


def test_create_mode_never_overwrites(vault: FilesystemVault, write_note: WriteNote) -> None:
    write_note("00-Inbox/a.md", "original\n")
    coach = _coach(vault, WriteMode.CREAR)
    snapshot = vault.read(_path("00-Inbox/a.md"))
    with pytest.raises(PermissionDeniedError) as info:
        coach.write(_path("00-Inbox/a.md"), "cambiado\n", expected=snapshot.version)
    assert info.value.reason == "crear_no_edita"
    assert coach.write(_path("00-Inbox/b.md"), "nueva\n", expected=None)


def test_append_mode_accepts_only_insertions_inside_the_section(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(DAILY, DAILY_TEXT)
    coach = _coach(vault, WriteMode.ANEXAR_SECCION)
    snapshot = vault.read(_path(DAILY))

    outside = DAILY_TEXT.replace("- bloque\n", "- bloque\n- 09:00 — colado\n")
    with pytest.raises(PermissionDeniedError) as info:
        coach.write(_path(DAILY), outside, expected=snapshot.version)
    assert info.value.reason == "fuera_de_seccion"

    rewritten = DAILY_TEXT.replace("- bloque", "- bloque cambiado")
    with pytest.raises(PermissionDeniedError):
        coach.write(_path(DAILY), rewritten + "- 09:00 — x\n", expected=snapshot.version)

    coach.write(_path(DAILY), DAILY_TEXT + "- 09:00 — bien\n", expected=snapshot.version)
    assert vault.read(_path(DAILY)).text.endswith("- 09:00 — bien\n")


def test_append_mode_cannot_create_notes_and_detects_concurrent_edits(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    coach = _coach(vault, WriteMode.ANEXAR_SECCION)
    with pytest.raises(PermissionDeniedError) as info:
        coach.write(_path(DAILY), "## Check-ins\n- x\n", expected=None)
    assert info.value.reason == "anexar_requiere_nota"

    write_note(DAILY, DAILY_TEXT)
    stale = vault.read(_path(DAILY)).version
    vault.write(_path(DAILY), DAILY_TEXT + "editado en Obsidian\n", expected=stale)
    with pytest.raises(VersionConflictError):
        coach.write(_path(DAILY), DAILY_TEXT + "- 09:00 — x\n", expected=stale)


def test_plan_mode_is_refused_until_phase_3(vault: FilesystemVault) -> None:
    jefe = PolicyVault(vault, samples.effective()["jefe"], WriteMode.PLAN)
    with pytest.raises(PermissionDeniedError) as info:
        jefe.write(_path("Semanal/2026-W44.md"), "plan\n", expected=None)
    assert info.value.reason == "plan_no_disponible"


def test_jefe_cannot_write_plan_folders_through_other_modes(vault: FilesystemVault) -> None:
    for mode in (WriteMode.CREAR, WriteMode.ANEXAR_SECCION):
        jefe = PolicyVault(vault, samples.effective()["jefe"], mode)
        with pytest.raises(PermissionDeniedError):
            jefe.write(_path("Semanal/2026-W44.md"), "plan\n", expected=None)


def test_non_markdown_targets_are_rejected(vault: FilesystemVault) -> None:
    with pytest.raises(PermissionDeniedError):
        _coach(vault, WriteMode.CREAR).write(_path("00-Inbox/x.txt"), "x", expected=None)
