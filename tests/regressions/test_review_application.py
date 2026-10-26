"""Regresiones de la revisión de la Fase 1 (casos de uso) y alcance de memoria-y-vigencia."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.archive import archive_matching, archive_task, restore_task
from lifeos.application.capture import capture
from lifeos.application.tasks import (
    ApplicationError,
    TaskQuery,
    create_task,
    find_task,
    query_tasks,
    scan,
    update_task,
)
from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.task import Area, Checkbox, Effort, TaskStatus
from lifeos.ports.vault import VaultPath, VersionConflictError

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 26, 9, 30, tzinfo=UTC)
A = "01-Universidad/redes.md"
TASK = "- [ ] Leer [id:: t-222222] [area:: universidad] [esfuerzo:: 1h]"


@pytest.fixture
def vault(tmp_path: Path) -> FilesystemVault:
    return FilesystemVault(tmp_path)


def _write(vault: FilesystemVault, path: str, text: str) -> None:
    vault.write(VaultPath.parse(path), text, expected=None)


# --- Inbox: el texto capturado no participa en el índice de tareas (code-reviewer HIGH) -----


def test_captured_text_cannot_poison_existing_task_ids(vault: FilesystemVault) -> None:
    _write(vault, A, TASK + "\n")
    capture(vault, "- [ ] pegado [id:: t-222222]", now=NOW)

    assert find_task(vault, TaskId("t-222222")).path == VaultPath.parse(A)
    update_task(vault, TaskId("t-222222"), checkbox=Checkbox.DONE)


def test_tasks_cannot_be_created_in_inbox(vault: FilesystemVault) -> None:
    with pytest.raises(ApplicationError):
        create_task(
            vault, VaultPath.parse("00-Inbox/x.md"), title="x", area=Area.SALUD, effort=Effort(30)
        )


# --- Escaneo robusto (architect / silent-failure-hunter HIGH) -------------------------------


def test_unreadable_note_is_reported_without_aborting_scan(
    vault: FilesystemVault, tmp_path: Path
) -> None:
    _write(vault, A, TASK + "\n")
    (tmp_path / "latin1.md").write_bytes("canción".encode("latin-1"))

    report = scan(vault)

    assert [t.task.id for t in report.tasks] == [TaskId("t-222222")]
    assert [(str(p.path), p.line_index) for p in report.problems] == [("latin1.md", None)]


def test_ids_in_malformed_lines_are_reserved_and_explained(vault: FilesystemVault) -> None:
    _write(vault, A, "- [ ] Pagar [id:: t-333333] 📅 2026-02-30\n")

    report = scan(vault)
    assert TaskId("t-333333") in report.reserved_ids
    with pytest.raises(ApplicationError, match="formato"):
        find_task(vault, TaskId("t-333333"))


# --- Carpetas protegidas sin distinguir mayúsculas (silent-failure-hunter MEDIUM) -----------


@pytest.mark.parametrize("path", ["_SISTEMA/x.md", "archivo/x.md", "_Logs/x.md", "00-inbox/x.md"])
def test_protected_roots_are_case_insensitive(vault: FilesystemVault, path: str) -> None:
    with pytest.raises(ApplicationError):
        create_task(vault, VaultPath.parse(path), title="x", area=Area.SALUD, effort=Effort(30))


def test_scan_skips_system_folder_regardless_of_case(vault: FilesystemVault) -> None:
    _write(vault, "_Sistema/reglas.md", "- [ ] Regla [id:: t-444444]\n")

    assert scan(vault).tasks == ()


# --- Archivo: no perder ediciones ni crear duplicados (silent-failure-hunter / code-reviewer)


def test_archive_refuses_when_archived_copy_differs_from_active_line(
    vault: FilesystemVault,
) -> None:
    _write(vault, A, TASK + " [avance:: 50%]\n")
    _write(vault, "Archivo/" + A, TASK + "\n")

    with pytest.raises(VersionConflictError):
        archive_task(vault, TaskId("t-222222"))
    assert "[avance:: 50%]" in vault.read(VaultPath.parse(A)).text


def test_restore_refuses_when_active_copy_exists_elsewhere(vault: FilesystemVault) -> None:
    _write(vault, "Archivo/" + A, TASK + "\n")
    _write(vault, "02-Proyectos/otro.md", TASK + "\n")

    with pytest.raises(ApplicationError):
        restore_task(vault, TaskId("t-222222"))


def test_restore_into_protected_root_is_rejected(vault: FilesystemVault) -> None:
    _write(vault, "Archivo/_sistema/x.md", TASK + "\n")

    with pytest.raises(ApplicationError):
        restore_task(vault, TaskId("t-222222"))


# --- Alcance de memoria-y-vigencia: cierre de ciclo y búsqueda histórica --------------------


def test_closing_a_cycle_archives_matching_tasks_without_completing_them(
    vault: FilesystemVault,
) -> None:
    _write(
        vault,
        A,
        TASK + " 📅 2026-07-01\n"
        "- [x] Lab [id:: t-333333] [area:: universidad] [esfuerzo:: 1h] 📅 2026-07-10\n"
        "- [ ] Futuro [id:: t-444444] [area:: universidad] [esfuerzo:: 1h] 📅 2026-12-01\n",
    )

    moved = archive_matching(vault, TaskQuery(area=Area.UNIVERSIDAD, due_to=date(2026, 7, 31)))

    assert sorted(t.value for t in moved) == ["t-222222", "t-333333"]
    archived = {r.task.id: r.task.status for r in query_tasks(vault, TaskQuery(include_archived=True)) if r.archived}
    assert archived[TaskId("t-222222")] is TaskStatus.PROPUESTA
    assert archived[TaskId("t-333333")] is TaskStatus.COMPLETADA
    assert [r.task.id for r in query_tasks(vault, TaskQuery())] == [TaskId("t-444444")]


def test_history_can_be_searched_by_topic_and_date(vault: FilesystemVault) -> None:
    _write(vault, "Archivo/" + A, TASK + " 📅 2026-03-01\n")
    _write(vault, "02-Proyectos/web.md", "- [ ] Leer docs [id:: t-555555] [area:: proyectos] [esfuerzo:: 1h]\n")

    found = query_tasks(
        vault, TaskQuery(text="LEER", include_archived=True, due_from=date(2026, 1, 1))
    )

    assert [r.task.id for r in found] == [TaskId("t-222222")]


def test_query_rejects_inverted_date_range() -> None:
    with pytest.raises(ValidationError, match="fecha"):
        TaskQuery(due_from=date(2026, 2, 1), due_to=date(2026, 1, 1))
