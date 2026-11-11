"""Revisión de propuestas por el propietario (F8-T02, RULES C.3 y C.5)."""

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.proposal_review import ProposalReview, ProposalReviewError
from lifeos.application.tasks import TaskQuery, find_task, query_tasks
from lifeos.domain.area_proposals import (
    AreaProposal,
    ProposalItem,
    ProposalKind,
    ProposalRef,
    ProposalStatus,
    parse_proposal_note,
    render_proposal,
)
from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.task import Area, Effort, Priority
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.logs import AuditResult
from lifeos.ports.vault import VaultPath
from tests.application.conftest import WriteNote
from tests.fakes import FakeClock, MemoryAuditLog

pytestmark = pytest.mark.integration

WEEK = IsoWeek.parse("2026-W44")
NOTE = "Propuestas/2026-W44/universidad.md"
TASK = "- [ ] Informe [id:: t-abc234] [area:: universidad] [esfuerzo:: 2h]\n"


def _proposal() -> AreaProposal:
    return AreaProposal(
        "universidad",
        Area.UNIVERSIDAD,
        WEEK,
        "heuristica",
        (
            ProposalItem(
                "p1", ProposalKind.PRIORIDAD, Priority.ALTA, "vence", task_id=TaskId("t-abc234")
            ),
            ProposalItem(
                "p2",
                ProposalKind.NUEVA_TAREA,
                Priority.MEDIA,
                "meta m1",
                title="Siguiente paso: Aprobar Redes",
                effort=Effort.parse("1h"),
            ),
        ),
    )


@pytest.fixture
def seeded(vault: FilesystemVault, write_note: WriteNote) -> FilesystemVault:
    write_note("01-Universidad/redes.md", TASK)
    write_note(NOTE, render_proposal(_proposal()))
    return vault


def _review(vault: FilesystemVault, audit: MemoryAuditLog | None = None) -> ProposalReview:
    return ProposalReview(vault, audit or MemoryAuditLog(), FakeClock())


def _replace(vault: FilesystemVault, path: str, text: str) -> None:
    target = VaultPath(path)
    vault.write(target, text, expected=vault.read(target).version)


def _statuses(vault: FilesystemVault) -> dict[str, ProposalStatus]:
    return {i.key: i.status for i in parse_proposal_note(vault.read(VaultPath(NOTE)).text)}


def test_lists_pending_items_with_task_context(seeded: FilesystemVault) -> None:
    items = _review(seeded).pending(WEEK)

    assert [str(i.ref) for i in items] == ["2026-W44/universidad/p1", "2026-W44/universidad/p2"]
    first = items[0]
    assert first.area is Area.UNIVERSIDAD
    assert first.task_title == "Informe"
    assert first.current_priority is Priority.MEDIA


def test_accept_priority_change_updates_task_and_marks_item(seeded: FilesystemVault) -> None:
    audit = MemoryAuditLog()

    _review(seeded, audit).accept(ProposalRef.parse("2026-W44/universidad/p1"))

    assert find_task(seeded, TaskId("t-abc234")).task.priority is Priority.ALTA
    assert _statuses(seeded)["p1"] is ProposalStatus.ACEPTADA
    assert {(r.actor, r.action, r.result) for r in audit.records} >= {
        ("propietario", "propuesta_aceptar", AuditResult.OK)
    }


def test_accept_new_task_creates_it_in_area_folder(seeded: FilesystemVault) -> None:
    result = _review(seeded).accept(ProposalRef.parse("2026-W44/universidad/p2"))

    assert result.task_id is not None
    created = find_task(seeded, result.task_id)
    assert str(created.path) == "01-Universidad/tareas.md"
    assert created.task.title == "Siguiente paso: Aprobar Redes"
    assert created.task.area is Area.UNIVERSIDAD
    assert _statuses(seeded)["p2"] is ProposalStatus.ACEPTADA


def test_reject_changes_nothing_but_the_mark(seeded: FilesystemVault) -> None:
    _review(seeded).reject(ProposalRef.parse("2026-W44/universidad/p1"))

    assert find_task(seeded, TaskId("t-abc234")).task.priority is Priority.MEDIA
    assert _statuses(seeded)["p1"] is ProposalStatus.RECHAZADA
    assert [str(i.ref) for i in _review(seeded).pending(WEEK)] == ["2026-W44/universidad/p2"]


def test_accepting_twice_is_rejected(seeded: FilesystemVault) -> None:
    review = _review(seeded)
    review.accept(ProposalRef.parse("2026-W44/universidad/p2"))

    with pytest.raises(ValidationError, match="ya está aceptada"):
        review.accept(ProposalRef.parse("2026-W44/universidad/p2"))
    assert len(query_tasks(seeded, TaskQuery(text="Siguiente paso"))) == 1


def test_priority_change_for_vanished_task_is_refused(
    seeded: FilesystemVault,
) -> None:
    _replace(seeded, "01-Universidad/redes.md", "sin tareas\n")

    with pytest.raises(ProposalReviewError, match="t-abc234"):
        _review(seeded).accept(ProposalRef.parse("2026-W44/universidad/p1"))
    assert _statuses(seeded)["p1"] is ProposalStatus.PENDIENTE


def test_priority_change_on_task_of_another_area_is_refused(
    seeded: FilesystemVault,
) -> None:
    _replace(
        seeded,
        "01-Universidad/redes.md",
        "- [ ] Informe [id:: t-abc234] [area:: proyectos] [esfuerzo:: 2h]\n",
    )

    with pytest.raises(ProposalReviewError, match="área"):
        _review(seeded).accept(ProposalRef.parse("2026-W44/universidad/p1"))


def test_completed_task_is_not_changed(seeded: FilesystemVault) -> None:
    _replace(
        seeded,
        "01-Universidad/redes.md",
        "- [x] Informe [id:: t-abc234] [area:: universidad] [esfuerzo:: 2h]\n",
    )

    with pytest.raises(ProposalReviewError, match="cerrada"):
        _review(seeded).accept(ProposalRef.parse("2026-W44/universidad/p1"))


def test_missing_note_is_a_clear_error(vault: FilesystemVault) -> None:
    with pytest.raises(ProposalReviewError, match="no existe"):
        _review(vault).accept(ProposalRef.parse("2026-W44/universidad/p1"))


def test_note_without_valid_area_header_is_refused(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    text = render_proposal(_proposal()).replace("[area:: universidad]", "[area:: salud]")
    write_note(NOTE, text)

    with pytest.raises(ProposalReviewError, match="área"):
        _review(vault).accept(ProposalRef.parse("2026-W44/universidad/p2"))


def test_pending_without_proposals_folder_is_empty(vault: FilesystemVault) -> None:
    assert _review(vault).pending(WEEK) == ()


def test_pending_lists_all_weeks_when_no_week_given(
    seeded: FilesystemVault, write_note: WriteNote
) -> None:
    other = render_proposal(_proposal()).replace("2026-W44", "2026-W45")
    write_note("Propuestas/2026-W45/universidad.md", other)

    refs = [str(i.ref) for i in _review(seeded).pending()]

    assert "2026-W45/universidad/p1" in refs
    assert "2026-W44/universidad/p1" in refs
