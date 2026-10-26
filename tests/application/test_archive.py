"""Archivo histórico reversible (F1-T06): mover a Archivo/ y restaurar sin pérdida; nunca borrar."""

from pathlib import Path

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.archive import archive_task, restore_task
from lifeos.application.tasks import ApplicationError, TaskNotFoundError, TaskQuery, query_tasks
from lifeos.domain.ids import TaskId
from lifeos.ports.vault import VaultPath

from .conftest import WriteNote

pytestmark = pytest.mark.integration

SOURCE = VaultPath.parse("01-Universidad/redes.md")
TARGET = VaultPath.parse("Archivo/01-Universidad/redes.md")
DONE = "- [x] Entregar lab 1 [id:: t-333333] [area:: universidad] [esfuerzo:: 1h] [foo:: bar]"
NOTE = f"# Redes\n- [ ] Leer [id:: t-222222] [area:: universidad] [esfuerzo:: 2h]\n{DONE}\n"
ID = TaskId("t-333333")


@pytest.fixture(autouse=True)
def seeded(write_note: WriteNote) -> None:
    write_note(SOURCE.value, NOTE)


def test_archive_moves_line_keeping_checkbox_id_and_unknown_fields(vault: FilesystemVault) -> None:
    destination = archive_task(vault, ID)

    assert destination == TARGET
    assert vault.read(TARGET).text == DONE + "\n"
    assert DONE not in vault.read(SOURCE).text
    assert vault.read(SOURCE).text.startswith("# Redes\n- [ ] Leer")


def test_archived_task_is_excluded_from_default_query(vault: FilesystemVault) -> None:
    archive_task(vault, ID)

    default = [r.task.id for r in query_tasks(vault, TaskQuery())]
    everything = [r for r in query_tasks(vault, TaskQuery(include_archived=True)) if r.task.id == ID]
    assert ID not in default
    assert len(everything) == 1 and everything[0].archived


def test_restore_returns_identical_line_to_original_note(vault: FilesystemVault) -> None:
    archive_task(vault, ID)

    restored_to = restore_task(vault, ID)

    assert restored_to == SOURCE
    assert vault.read(SOURCE).text.splitlines()[-1] == DONE
    assert vault.read(TARGET).text == ""


def test_archive_and_restore_never_delete_files(vault: FilesystemVault, root: Path) -> None:
    archive_task(vault, ID)
    restore_task(vault, ID)
    archive_task(vault, TaskId("t-222222"))

    assert (root / SOURCE.value).exists()
    assert (root / TARGET.value).exists()


def test_archive_is_idempotent_after_crash_between_steps(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    # Simula una caída tras copiar a Archivo/ y antes de quitar la línea del origen.
    write_note(TARGET.value, DONE + "\n")

    archive_task(vault, ID)

    assert DONE not in vault.read(SOURCE).text
    assert vault.read(TARGET).text == DONE + "\n"


def test_archive_already_archived_task_is_a_no_op(vault: FilesystemVault) -> None:
    archive_task(vault, ID)
    before = vault.read(TARGET).text

    assert archive_task(vault, ID) == TARGET
    assert vault.read(TARGET).text == before


def test_restore_task_that_is_not_archived_raises(vault: FilesystemVault) -> None:
    with pytest.raises(TaskNotFoundError):
        restore_task(vault, ID)


def test_archive_unknown_task_raises(vault: FilesystemVault) -> None:
    with pytest.raises(TaskNotFoundError):
        archive_task(vault, TaskId("t-999999"))


def test_archive_rejects_task_with_nested_subtasks(vault: FilesystemVault, write_note: WriteNote) -> None:
    write_note("02-Proyectos/web.md", "- [ ] Padre [id:: t-444444]\n  - [ ] Hija [id:: t-555555]\n")

    with pytest.raises(ApplicationError, match="subtareas"):
        archive_task(vault, TaskId("t-444444"))
