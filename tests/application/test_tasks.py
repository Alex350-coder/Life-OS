"""Crear, consultar y actualizar tareas en una bóveda de prueba (F1-T05)."""

from datetime import date
from pathlib import Path

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.tasks import (
    ApplicationError,
    LocatedTask,
    TaskNotFoundError,
    TaskQuery,
    create_task,
    ensure_id,
    find_task,
    query_tasks,
    scan,
    update_task,
)
from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.integrity import DuplicateTaskIdError
from lifeos.domain.task import Area, Checkbox, Effort, TaskStatus
from lifeos.ports.vault import VaultPath, Version, VersionConflictError

from .conftest import WriteNote

pytestmark = pytest.mark.integration

UNI = VaultPath.parse("01-Universidad/redes.md")
NOTE = (
    "# Redes\n"
    "- [ ] Leer capítulo 4 [id:: t-222222] [area:: universidad] [esfuerzo:: 2h] 📅 2026-10-30\n"
    "- [x] Entregar lab 1 [id:: t-333333] [area:: universidad] [esfuerzo:: 1h]\n"
    "- [ ] Sin id [area:: universidad] [esfuerzo:: 30m]\n"
)


@pytest.fixture
def seeded(write_note: WriteNote) -> None:
    write_note(UNI.value, NOTE)
    write_note(
        "07-Cotidiano/casa.md",
        "- [ ] Pagar luz [id:: t-444444] [area:: cotidiano] [esfuerzo:: 15m] 📅 2026-11-02\n",
    )
    write_note(
        "Archivo/01-Universidad/redes.md",
        "- [x] Lab 0 [id:: t-555555] [area:: universidad] [esfuerzo:: 1h]\n",
    )
    write_note("_sistema/reglas.md", "- [ ] Regla del sistema [id:: t-666666]\n")


def _ids(results: tuple[LocatedTask, ...]) -> list[str]:
    return [r.task.id.value for r in results if r.task.id is not None]


# --- Escaneo e integridad -------------------------------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_scan_reads_vault_but_skips_system_and_logs(vault: FilesystemVault) -> None:
    report = scan(vault)

    assert "t-666666" not in _ids(report.tasks)
    assert {"t-222222", "t-444444", "t-555555"} <= set(_ids(report.tasks))
    assert report.problems == ()
    assert report.duplicates == ()


def test_scan_reports_parse_problems_and_duplicates(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note("a.md", "- [ ] Mala [esfuerzo:: 0h]\n- [ ] Uno [id:: t-777777]\n")
    write_note("b.md", "- [ ] Dos [id:: t-777777]\n")

    report = scan(vault)

    assert [(str(p.path), p.line_index) for p in report.problems] == [("a.md", 0)]
    assert len(report.duplicates) == 1
    assert report.duplicates[0].task_id == TaskId("t-777777")


# --- Consultas ------------------------------------------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_default_query_excludes_archived_tasks(vault: FilesystemVault) -> None:
    ids = _ids(query_tasks(vault, TaskQuery()))

    assert "t-555555" not in ids
    assert {"t-222222", "t-333333", "t-444444"} <= set(ids)


@pytest.mark.usefixtures("seeded")
def test_query_can_include_archived_and_marks_them(vault: FilesystemVault) -> None:
    results = query_tasks(vault, TaskQuery(include_archived=True))

    archived = [r for r in results if r.archived]
    assert _ids(tuple(archived)) == ["t-555555"]


@pytest.mark.usefixtures("seeded")
def test_query_filters_by_area_status_and_due_range(vault: FilesystemVault) -> None:
    assert _ids(query_tasks(vault, TaskQuery(area=Area.COTIDIANO))) == ["t-444444"]
    assert _ids(query_tasks(vault, TaskQuery(status=TaskStatus.COMPLETADA))) == ["t-333333"]
    in_range = query_tasks(vault, TaskQuery(due_from=date(2026, 10, 29), due_to=date(2026, 10, 31)))
    assert _ids(in_range) == ["t-222222"]


@pytest.mark.usefixtures("seeded")
def test_find_task_by_id(vault: FilesystemVault) -> None:
    located = find_task(vault, TaskId("t-444444"))

    assert located.path == VaultPath.parse("07-Cotidiano/casa.md")
    assert located.index == 0
    with pytest.raises(TaskNotFoundError):
        find_task(vault, TaskId("t-999999"))


def test_find_task_with_duplicate_id_raises(vault: FilesystemVault, write_note: WriteNote) -> None:
    write_note("a.md", "- [ ] Uno [id:: t-777777]\n")
    write_note("b.md", "- [ ] Dos [id:: t-777777]\n")

    with pytest.raises(DuplicateTaskIdError):
        find_task(vault, TaskId("t-777777"))


# --- Creación -------------------------------------------------------------------------------


def test_create_task_assigns_unique_id_and_appends_line(vault: FilesystemVault) -> None:
    located = create_task(
        vault,
        UNI,
        title="Repasar subredes",
        area=Area.UNIVERSIDAD,
        effort=Effort(90),
        due=date(2026, 11, 3),
    )

    assert located.task.id is not None
    text = vault.read(UNI).text
    expected = (
        f"- [ ] Repasar subredes [id:: {located.task.id}] "
        "[area:: universidad] [esfuerzo:: 1.5h] 📅 2026-11-03\n"
    )
    assert text == expected


@pytest.mark.usefixtures("seeded")
def test_create_task_appends_to_existing_note_preserving_content(vault: FilesystemVault) -> None:
    create_task(vault, UNI, title="Nueva", area=Area.UNIVERSIDAD, effort=Effort(30))

    text = vault.read(UNI).text
    assert text.startswith(NOTE)
    assert text.count("\n") == NOTE.count("\n") + 1


@pytest.mark.parametrize(
    "path", ["Archivo/x.md", "_sistema/x.md", "_logs/x.md", "00-Inbox/no-es-nota.txt"]
)
def test_create_task_rejects_protected_or_non_markdown_paths(
    vault: FilesystemVault, path: str
) -> None:
    with pytest.raises(ApplicationError):
        create_task(vault, VaultPath.parse(path), title="x", area=Area.SALUD, effort=Effort(30))


@pytest.mark.parametrize(
    "title", ["Con campo [area:: salud]", "Con fecha 📅 2026-10-30", "dos\nlíneas", "  "]
)
def test_create_task_rejects_titles_that_would_inject_fields(
    vault: FilesystemVault, title: str
) -> None:
    with pytest.raises(ValidationError):
        create_task(vault, UNI, title=title, area=Area.SALUD, effort=Effort(30))


# --- Actualización --------------------------------------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_update_task_changes_only_target_line(vault: FilesystemVault) -> None:
    updated = update_task(
        vault, TaskId("t-222222"), checkbox=Checkbox.DONE, fields={"avance": "100%"}
    )

    assert updated.task.status is TaskStatus.COMPLETADA
    lines = vault.read(UNI).text.splitlines()
    assert lines[1].startswith("- [x] Leer capítulo 4 [id:: t-222222]")
    assert lines[1].endswith("📅 2026-10-30 [avance:: 100%]")
    assert lines[2:] == NOTE.splitlines()[2:]


@pytest.mark.usefixtures("seeded")
def test_update_task_rejects_id_change_and_invalid_values(vault: FilesystemVault) -> None:
    with pytest.raises(ValidationError):
        update_task(vault, TaskId("t-222222"), fields={"id": "t-888888"})
    with pytest.raises(ValidationError):
        update_task(vault, TaskId("t-222222"), fields={"esfuerzo": "0h"})
    assert vault.read(UNI).text == NOTE


@pytest.mark.usefixtures("seeded")
def test_update_archived_task_is_rejected(vault: FilesystemVault) -> None:
    with pytest.raises(ApplicationError, match="archivada"):
        update_task(vault, TaskId("t-555555"), checkbox=Checkbox.PENDING)


@pytest.mark.usefixtures("seeded")
def test_update_detects_concurrent_edit(
    vault: FilesystemVault, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write = vault.write

    def user_edits_then_write(path: VaultPath, text: str, *, expected: Version | None) -> Version:
        # El usuario guarda la nota en Obsidian entre nuestra lectura y nuestra escritura.
        (root / path.value).write_text(NOTE + "- [ ] Escrita en Obsidian\n", encoding="utf-8")
        return original_write(path, text, expected=expected)

    monkeypatch.setattr(vault, "write", user_edits_then_write)
    with pytest.raises(VersionConflictError):
        update_task(vault, TaskId("t-222222"), checkbox=Checkbox.DONE)
    assert (root / UNI.value).read_text(encoding="utf-8").endswith("Escrita en Obsidian\n")


@pytest.mark.usefixtures("seeded")
def test_ensure_id_assigns_id_without_altering_rest_of_line(vault: FilesystemVault) -> None:
    located = ensure_id(vault, UNI, 3)

    assert located.task.id is not None
    lines = vault.read(UNI).text.splitlines()
    assert (
        lines[3] == f"- [ ] Sin id [id:: {located.task.id}] [area:: universidad] [esfuerzo:: 30m]"
    )
    assert lines[:3] == NOTE.splitlines()[:3]


@pytest.mark.usefixtures("seeded")
def test_ensure_id_keeps_existing_id_and_rejects_non_task_line(vault: FilesystemVault) -> None:
    assert ensure_id(vault, UNI, 1).task.id == TaskId("t-222222")
    assert vault.read(UNI).text == NOTE
    with pytest.raises(TaskNotFoundError):
        ensure_id(vault, UNI, 0)
