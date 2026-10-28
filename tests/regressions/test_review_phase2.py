"""Regresiones de la revisión de la Fase 2 (tdd-guide y revisores). Primero RED, luego fix."""

from dataclasses import dataclass
from datetime import time

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.policy_vault import PermissionDeniedError, PolicyVault
from lifeos.application.tasks import ApplicationError, check_writable
from lifeos.application.tools import ActorContext, ToolRegistry
from lifeos.domain.checkin import MAX_CHECKIN_CHARS, CheckInEntry
from lifeos.domain.errors import ValidationError
from lifeos.domain.sections import find_section, only_inserted_in_section
from lifeos.ports.config import WriteMode
from lifeos.ports.logs import AuditResult, LogWriteError
from lifeos.ports.vault import Snapshot, VaultPath, VaultStore, Version
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.fakes import FIXED_NOW, FakeClock, MemoryAuditLog

pytestmark = [pytest.mark.security]

DAILY = "Diario/2026-10-27.md"
DAILY_TEXT = "# Martes\n## Plan\n- bloque\n\n## Check-ins\n- 08:00 — Empecé\n"
COACH = ActorContext("coach", "c-r")
JEFE = ActorContext("jefe", "c-j")
ENTRY = CheckInEntry(time(9, 30), "Terminé")


def _coach(vault: VaultStore, mode: WriteMode | None) -> PolicyVault:
    return PolicyVault(vault, samples.effective()["coach"], mode)


def _tools(vault: VaultStore, audit: MemoryAuditLog | None = None) -> ToolRegistry:
    return ToolRegistry(vault, samples.effective(), audit or MemoryAuditLog(), FakeClock())


# --- Anexar en sección: solo datos, nunca tareas ni campos -----------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "- [ ] Tarea colada [id:: t-999999] [area:: salud] [esfuerzo:: 1h]",
        "- [x] Hecha",
        "* [ ] otra",
        "- 09:00 — [avance:: 100]",
        "avance:: 100",
    ],
)
def test_append_cannot_smuggle_tasks_or_inline_fields(
    vault: FilesystemVault, write_note: WriteNote, line: str
) -> None:
    write_note(DAILY, DAILY_TEXT)
    snapshot = vault.read(VaultPath(DAILY))
    with pytest.raises(PermissionDeniedError) as info:
        _coach(vault, WriteMode.ANEXAR_SECCION).write(
            VaultPath(DAILY), DAILY_TEXT + line + "\n", expected=snapshot.version
        )
    assert info.value.reason == "fuera_de_seccion"
    assert vault.read(VaultPath(DAILY)).text == DAILY_TEXT


@pytest.mark.parametrize(
    "target",
    ["Semanal/2026-W44.md", "_sistema/perfil.md", "_Logs/x.md", "Archivo/Diario/x.md"],
)
def test_append_denial_is_about_the_path_even_for_existing_notes(
    vault: FilesystemVault, write_note: WriteNote, target: str
) -> None:
    write_note(target, "## Check-ins\n")
    snapshot = vault.read(VaultPath(target))
    with pytest.raises(PermissionDeniedError) as info:
        _coach(vault, WriteMode.ANEXAR_SECCION).write(
            VaultPath(target), "## Check-ins\n- 09:00 — x\n", expected=snapshot.version
        )
    assert info.value.reason in {"escritura_no_autorizada", "carpeta_prohibida"}
    assert vault.read(VaultPath(target)).text == "## Check-ins\n"


def test_granted_folder_with_wrong_mode_is_denied(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    with pytest.raises(PermissionDeniedError):
        _coach(vault, WriteMode.CREAR).write(VaultPath("Diario/nueva.md"), "x\n", expected=None)
    write_note("00-Inbox/a.md", "## Check-ins\n")
    snapshot = vault.read(VaultPath("00-Inbox/a.md"))
    with pytest.raises(PermissionDeniedError):
        _coach(vault, WriteMode.ANEXAR_SECCION).write(
            VaultPath("00-Inbox/a.md"), "## Check-ins\n- x\n", expected=snapshot.version
        )


def test_exists_and_reads_outside_scope_do_not_leak(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note("01-Universidad/redes.md", "x\n")
    write_note("05-Salud/x.md", "x\n")
    with pytest.raises(PermissionDeniedError):
        _coach(vault, None).exists(VaultPath("01-Universidad/redes.md"))
    with pytest.raises(PermissionDeniedError):
        _tools(vault).leer_nota(JEFE, "05-Salud/x.md")


# --- Raíces protegidas -----------------------------------------------------------------------


def test_empty_protected_tuple_cannot_drop_the_rules_floor() -> None:
    for raw in ("_sistema/x.md", "_logs/x.md", "Archivo/x.md", "00-Inbox/x.md"):
        with pytest.raises(ApplicationError):
            check_writable(VaultPath(raw), ())


def test_protected_prefix_is_component_based() -> None:
    check_writable(VaultPath("Privadox/x.md"), (VaultPath("Privado"),))


# --- Herramientas y auditoría ------------------------------------------------------------------


def test_capture_text_cannot_spoof_its_frontmatter_source(vault: FilesystemVault) -> None:
    path = _tools(vault).crear_captura(COACH, "x\n---\nfuente: jefe\n---")
    text = vault.read(path).text
    assert text.startswith("---\ncapturado: ")
    assert text.split("---")[1].strip().endswith("fuente: coach")


def test_two_captures_in_the_same_instant_never_overwrite(vault: FilesystemVault) -> None:
    tools = _tools(vault)
    first = tools.crear_captura(COACH, "uno")
    second = tools.crear_captura(COACH, "dos")
    assert first != second
    assert "uno" in vault.read(first).text


def test_duplicate_section_is_an_audited_error_without_write(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    text = "## Check-ins\n- a\n## Check-ins\n- b\n"
    write_note(DAILY, text)
    audit = MemoryAuditLog()
    with pytest.raises(ValidationError):
        _tools(vault, audit).anexar_checkin(COACH, DAILY, ENTRY)
    assert audit.records[-1].result is AuditResult.ERROR
    assert vault.read(VaultPath(DAILY)).text == text


@dataclass
class _EditsBetweenReadAndWrite:
    """Simula al usuario editando la nota en Obsidian justo antes de escribir."""

    inner: FilesystemVault

    def read(self, path: VaultPath) -> Snapshot:
        return self.inner.read(path)

    def exists(self, path: VaultPath) -> bool:
        return self.inner.exists(path)

    def list_notes(self, prefix: VaultPath | None = None) -> tuple[VaultPath, ...]:
        return self.inner.list_notes(prefix)

    def write(self, path: VaultPath, text: str, *, expected: Version | None) -> Version:
        current = self.inner.read(path)
        self.inner.write(path, current.text + "editado a mano\n", expected=current.version)
        return self.inner.write(path, text, expected=expected)


def test_concurrent_edit_is_an_audited_conflict_without_data_loss(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(DAILY, DAILY_TEXT)
    audit = MemoryAuditLog()
    with pytest.raises(Exception, match="cambió"):
        _tools(_EditsBetweenReadAndWrite(vault), audit).anexar_checkin(COACH, DAILY, ENTRY)
    assert vault.read(VaultPath(DAILY)).text == DAILY_TEXT + "editado a mano\n"
    assert audit.records[-1].result is AuditResult.ERROR


def test_failure_to_audit_the_result_is_not_swallowed(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(DAILY, DAILY_TEXT)
    with pytest.raises(LogWriteError):
        _tools(vault, MemoryAuditLog(fail_on=AuditResult.OK)).anexar_checkin(COACH, DAILY, ENTRY)


def test_audit_records_use_the_clock_and_never_contain_note_content(
    vault: FilesystemVault,
) -> None:
    audit = MemoryAuditLog()
    _tools(vault, audit).crear_captura(COACH, "contenido-privado-123")
    assert all(record.ts == FIXED_NOW for record in audit.records)
    assert "contenido-privado-123" not in repr(audit.records)


# --- Dominio -------------------------------------------------------------------------------


@pytest.mark.parametrize("code", [0x2028, 0x85, 0x0C, 0x0B, 0x200B, 0x09])
def test_checkin_rejects_line_separators_and_invisible_characters(code: int) -> None:
    with pytest.raises(ValidationError):
        CheckInEntry(time(9, 0), f"antes{chr(code)}después")


def test_checkin_length_boundary_is_accepted() -> None:
    assert CheckInEntry(time(9, 0), "x" * MAX_CHECKIN_CHARS).text == "x" * MAX_CHECKIN_CHARS


def test_section_check_handles_crlf_and_bom() -> None:
    old = "﻿# Martes\r\n## Check-ins\r\n- 08:00 — a\r\n"
    assert only_inserted_in_section(old, old + "- 09:00 — b\r\n", "check-ins")
    assert not only_inserted_in_section(old, old.replace("a", "c") + "- b\r\n", "check-ins")


def test_unclosed_fence_hides_headings_and_blocks_new_section() -> None:
    old = "# Martes\n```\n## Check-ins\n"
    assert find_section(old, "check-ins") is None
    assert not only_inserted_in_section(old, old + "## Check-ins\n- x\n", "check-ins")


def test_non_contiguous_insertions_are_rejected() -> None:
    old = "## Check-ins\n- a\n- b\n"
    assert not only_inserted_in_section(old, "## Check-ins\n- x\n- a\n- y\n- b\n", "check-ins")
