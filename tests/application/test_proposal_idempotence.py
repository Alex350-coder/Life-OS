"""Aceptar una propuesta es idempotente aunque el proceso falle entre aplicar y marcar (DT-29).

La tarea creada lleva su origen (`[propuesta:: <semana>/<nota>/<clave>]`). Repetir la
aceptación encuentra esa tarea, no crea otra y solo marca la propuesta.
"""

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.proposal_review import ProposalAppliedButUnmarkedError, ProposalReview
from lifeos.application.tasks import (
    ApplicationError,
    TaskQuery,
    create_task,
    find_by_origin,
    query_tasks,
)
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
from lifeos.domain.task import Area, Effort, Priority
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.logs import AuditResult
from lifeos.ports.vault import Snapshot, VaultIOError, VaultPath, Version
from tests.application.conftest import WriteNote
from tests.fakes import FakeClock, MemoryAuditLog

pytestmark = pytest.mark.integration

WEEK = IsoWeek.parse("2026-W44")
NOTE = "Propuestas/2026-W44/universidad.md"
REF = ProposalRef.parse("2026-W44/universidad/p1")


class _FailsMarkingOnce:
    """Bóveda que falla una vez al escribir la nota de propuestas: simula la caída del proceso
    justo después de aplicar el cambio y antes de marcarlo."""

    def __init__(self, inner: FilesystemVault) -> None:
        self._inner = inner
        self._failed = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def read(self, path: VaultPath) -> Snapshot:
        return self._inner.read(path)

    def write(self, path: VaultPath, text: str, *, expected: Version | None) -> Version:
        if path.value == NOTE and not self._failed:
            self._failed = True
            raise VaultIOError("caída simulada")
        return self._inner.write(path, text, expected=expected)


def _seed(write_note: WriteNote) -> None:
    item = ProposalItem(
        "p1",
        ProposalKind.NUEVA_TAREA,
        Priority.MEDIA,
        "meta m1",
        title="Siguiente paso: Aprobar Redes",
        effort=Effort.parse("1h"),
    )
    write_note(
        NOTE,
        render_proposal(AreaProposal("universidad", Area.UNIVERSIDAD, WEEK, "heuristica", (item,))),
    )


def _titles(vault: FilesystemVault) -> list[str]:
    return [t.task.title for t in query_tasks(vault, TaskQuery(area=Area.UNIVERSIDAD))]


def test_new_task_records_its_proposal_origin(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    _seed(write_note)

    result = ProposalReview(vault, MemoryAuditLog(), FakeClock()).accept(REF)

    located = find_by_origin(vault, str(REF))
    assert located is not None
    assert located.task.id == result.task_id
    assert "[propuesta:: 2026-W44/universidad/p1]" in located.line.raw


def test_retry_after_crash_between_apply_and_mark_does_not_duplicate(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    _seed(write_note)
    audit = MemoryAuditLog()
    with pytest.raises(ProposalAppliedButUnmarkedError):
        ProposalReview(_FailsMarkingOnce(vault), audit, FakeClock()).accept(REF)  # type: ignore[arg-type]

    retried = ProposalReview(vault, audit, FakeClock()).accept(REF)

    assert _titles(vault) == ["Siguiente paso: Aprobar Redes"]
    assert retried.task_id == find_by_origin(vault, str(REF)).task.id  # type: ignore[union-attr]
    statuses = {i.key: i.status for i in parse_proposal_note(vault.read(VaultPath(NOTE)).text)}
    assert statuses["p1"] is ProposalStatus.ACEPTADA
    final = audit.records[-1]
    assert final.result is AuditResult.OK
    assert final.reason == f"{retried.task_id} ya_aplicada"


def test_origin_survives_archiving_and_still_prevents_duplicate(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    _seed(write_note)
    write_note(
        "Archivo/01-Universidad/tareas.md",
        "- [x] Siguiente paso: Aprobar Redes [id:: t-abc234] [area:: universidad] "
        "[esfuerzo:: 1h] [propuesta:: 2026-W44/universidad/p1]\n",
    )

    result = ProposalReview(vault, MemoryAuditLog(), FakeClock()).accept(REF)

    assert str(result.task_id) == "t-abc234"
    assert _titles(vault) == []  # no se creó otra tarea activa


def test_create_task_rejects_a_malformed_origin(vault: FilesystemVault) -> None:
    with pytest.raises(ValidationError):
        create_task(
            vault,
            VaultPath("01-Universidad/tareas.md"),
            title="Algo",
            area=Area.UNIVERSIDAD,
            effort=Effort.parse("1h"),
            origin="2026-W44/universidad/p1] [estado:: completada",
        )


def test_unreadable_note_blocks_the_accept_instead_of_duplicating(
    vault: FilesystemVault, write_note: WriteNote, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(write_note)
    write_note("01-Universidad/tareas.md", "- [ ] Otra [id:: t-abc235] [area:: universidad]\n")
    original = vault.read

    def flaky(path: VaultPath) -> Snapshot:
        if path.value == "01-Universidad/tareas.md":
            raise VaultIOError("bloqueada por la sincronización")
        return original(path)

    monkeypatch.setattr(vault, "read", flaky)
    audit = MemoryAuditLog()

    with pytest.raises(ApplicationError, match=r"no se pudieron leer 01-Universidad/tareas.md"):
        ProposalReview(vault, audit, FakeClock()).accept(REF)

    monkeypatch.undo()
    assert _titles(vault) == ["Otra"]  # no se creó nada
    assert audit.records[-1].result is AuditResult.RECHAZADO


def test_malformed_line_with_the_origin_blocks_the_accept(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    _seed(write_note)
    write_note(
        "01-Universidad/tareas.md",
        "- [ ] Siguiente paso [id:: t-abc234] [area:: universidad] [esfuerzo:: mucho] "
        "[propuesta:: 2026-W44/universidad/p1]\n",
    )

    with pytest.raises(ApplicationError, match="errores de formato"):
        ProposalReview(vault, MemoryAuditLog(), FakeClock()).accept(REF)


def test_two_tasks_with_the_same_origin_are_reported(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    line = (
        "- [ ] Paso [id:: {}] [area:: universidad] [esfuerzo:: 1h] "
        "[propuesta:: 2026-W44/universidad/p1]\n"
    )
    write_note("01-Universidad/a.md", line.format("t-abc234"))
    write_note("01-Universidad/b.md", line.format("t-abc235"))

    with pytest.raises(ApplicationError, match="varias tareas"):
        find_by_origin(vault, str(REF))
