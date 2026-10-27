"""F2-T03: herramientas con permisos efectivos y auditoría de cada escritura (SEC-04)."""

from datetime import time

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.policy_vault import PermissionDeniedError
from lifeos.application.tasks import TaskQuery
from lifeos.application.tools import ActorContext, ToolRegistry
from lifeos.domain.checkin import CheckInEntry
from lifeos.ports.logs import AuditResult, LogWriteError
from lifeos.ports.vault import NoteNotFoundError, VaultPath
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.fakes import FakeClock, MemoryAuditLog

pytestmark = [pytest.mark.integration, pytest.mark.security]

DAILY = "Diario/2026-10-27.md"
DAILY_TEXT = (
    "# Martes\r\n## Plan\r\n- bloque\r\n\r\n## Check-ins\r\n- 08:00 — Empecé\r\n\r\n## Notas\r\n"
)
COACH = ActorContext(agent_id="coach", correlation_id="c-1")
JEFE = ActorContext(agent_id="jefe", correlation_id="c-2")
ENTRY = CheckInEntry(time(9, 30), "Terminé la lectura")


def _registry(vault: FilesystemVault, audit: MemoryAuditLog | None = None) -> ToolRegistry:
    return ToolRegistry(vault, samples.effective(), audit or MemoryAuditLog(), FakeClock())


def _results(audit: MemoryAuditLog) -> list[tuple[str, str, str, AuditResult]]:
    return [(r.actor, r.action, r.target, r.result) for r in audit.records]


# --- Lectura -------------------------------------------------------------------------------


def test_leer_nota_respects_read_scope(vault: FilesystemVault, write_note: WriteNote) -> None:
    write_note(DAILY, DAILY_TEXT)
    write_note("01-Universidad/redes.md", "solo jefe\n")
    audit = MemoryAuditLog()
    tools = _registry(vault, audit)

    assert tools.leer_nota(COACH, DAILY) == DAILY_TEXT
    with pytest.raises(PermissionDeniedError):
        tools.leer_nota(COACH, "01-Universidad/redes.md")
    assert _results(audit) == [
        ("coach", "leer_nota", "01-Universidad/redes.md", AuditResult.RECHAZADO)
    ]


def test_listar_tareas_only_sees_readable_notes(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    task = "- [ ] Leer [id:: t-222222] [area:: universidad] [esfuerzo:: 1h]\n"
    write_note("01-Universidad/redes.md", task)
    write_note("Diario/2026-10-27.md", task.replace("t-222222", "t-333333"))
    tools = _registry(vault)
    coach_ids = [str(t.task.id) for t in tools.listar_tareas(COACH, TaskQuery())]
    jefe_ids = [t.task.id for t in tools.listar_tareas(JEFE, TaskQuery())]
    assert coach_ids == ["t-333333"]
    assert sorted(str(i) for i in jefe_ids) == ["t-222222", "t-333333"]


# --- crear_captura ----------------------------------------------------------------------------


def test_crear_captura_writes_inbox_note_with_agent_source_and_audits(
    vault: FilesystemVault,
) -> None:
    audit = MemoryAuditLog()
    path = _registry(vault, audit).crear_captura(COACH, "Comprar pilas")
    assert path.value.startswith("00-Inbox/")
    assert "fuente: coach" in vault.read(path).text
    assert _results(audit) == [
        ("coach", "crear_captura", "00-Inbox", AuditResult.INTENTO),
        ("coach", "crear_captura", path.value, AuditResult.OK),
    ]
    assert {r.correlation_id for r in audit.records} == {"c-1"}


# --- anexar_checkin ---------------------------------------------------------------------------


def test_anexar_checkin_inserts_inside_section_preserving_everything_else(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(DAILY, DAILY_TEXT)
    audit = MemoryAuditLog()
    _registry(vault, audit).anexar_checkin(COACH, DAILY, ENTRY)
    expected = DAILY_TEXT.replace(
        "- 08:00 — Empecé\r\n", "- 08:00 — Empecé\r\n- 09:30 — Terminé la lectura\r\n"
    )
    assert vault.read(VaultPath(DAILY)).text == expected
    assert [r.result for r in audit.records] == [AuditResult.INTENTO, AuditResult.OK]


def test_anexar_checkin_adds_missing_section_at_the_end(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(DAILY, "# Martes\n## Plan\n- bloque")
    _registry(vault).anexar_checkin(COACH, DAILY, ENTRY)
    assert vault.read(VaultPath(DAILY)).text == (
        "# Martes\n## Plan\n- bloque\n\n## Check-ins\n- 09:30 — Terminé la lectura\n"
    )


def test_anexar_checkin_never_creates_the_daily_note(vault: FilesystemVault) -> None:
    audit = MemoryAuditLog()
    with pytest.raises(NoteNotFoundError):
        _registry(vault, audit).anexar_checkin(COACH, DAILY, ENTRY)
    assert not vault.list_notes()
    assert audit.records[-1].result is AuditResult.ERROR


@pytest.mark.parametrize(
    "target",
    [
        "Semanal/2026-W44.md",
        "semanal/2026-W44.md",
        "_sistema/perfil.md",
        "_logs/x.md",
        "Archivo/Diario/x.md",
        "Diario/../Semanal/x.md",
        "C:/fuera.md",
    ],
)
def test_coach_checkin_outside_daily_notes_is_denied_and_audited(
    vault: FilesystemVault, write_note: WriteNote, target: str
) -> None:
    for raw in ("Semanal/2026-W44.md", "_sistema/perfil.md"):
        write_note(raw, "## Check-ins\n")
    before = {p: vault.read(p).text for p in vault.list_notes()}
    audit = MemoryAuditLog()
    with pytest.raises(PermissionDeniedError):
        _registry(vault, audit).anexar_checkin(COACH, target, ENTRY)
    assert {p: vault.read(p).text for p in vault.list_notes()} == before
    assert audit.records[-1].result is AuditResult.RECHAZADO
    assert audit.records[-1].reason


def test_jefe_cannot_use_a_tool_it_was_not_granted(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(DAILY, DAILY_TEXT)
    audit = MemoryAuditLog()
    with pytest.raises(PermissionDeniedError) as info:
        _registry(vault, audit).anexar_checkin(JEFE, DAILY, ENTRY)
    assert info.value.reason == "herramienta_no_autorizada"
    assert _results(audit) == [("jefe", "anexar_checkin", DAILY, AuditResult.RECHAZADO)]


def test_unknown_agent_is_denied(vault: FilesystemVault) -> None:
    with pytest.raises(PermissionDeniedError):
        _registry(vault).crear_captura(ActorContext("intruso", "c-3"), "x")


# --- Auditoría con fallo cerrado ------------------------------------------------------------


def test_if_the_intent_cannot_be_audited_nothing_is_written(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(DAILY, DAILY_TEXT)
    audit = MemoryAuditLog(fail_on=AuditResult.INTENTO)
    with pytest.raises(LogWriteError):
        _registry(vault, audit).anexar_checkin(COACH, DAILY, ENTRY)
    assert vault.read(VaultPath(DAILY)).text == DAILY_TEXT
    with pytest.raises(LogWriteError):
        _registry(vault, audit).crear_captura(COACH, "x")
    assert vault.list_notes() == (VaultPath(DAILY),)
