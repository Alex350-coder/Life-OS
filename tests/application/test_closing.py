"""Cierre diario con confirmación (F4-T03): sin confirmación no se modifica nada.

La vista previa no escribe ni audita. Al confirmar, el propietario actualiza sus tareas (actor
`propietario`, auditado), el resumen va a `## Cierre` y el Coach propone al Jefe reubicar los
bloques no realizados.
"""

from datetime import datetime

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.closing import ClosingIncompleteError, DailyClosing
from lifeos.application.coach import CoachSession, NoDailyPlanError
from lifeos.application.tasks import ApplicationError
from lifeos.application.tools import ToolRegistry
from lifeos.domain.adjustments import AdjustmentKind, parse_adjustments
from lifeos.domain.closing import AnswerKind, ClosingAnswer
from lifeos.domain.errors import ValidationError
from lifeos.domain.task import Effort, Progress
from lifeos.ports.logs import AuditResult
from lifeos.ports.vault import VaultPath, VersionConflictError
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.daily_builders import TASKS, monday_note
from tests.fakes import FakeClock, MemoryAuditLog
from tests.planning_builders import MON, at, tid
from tests.planning_samples import HORARIO

pytestmark = [pytest.mark.integration]

MONDAY = VaultPath.parse("Diario/2026-11-02.md")
TASKS_NOTE = VaultPath.parse("01-Universidad/redes.md")
EVENING = at(MON, "21:00")
A, B, C = tid("t-aaaaaa"), tid("t-bbbbbb"), tid("t-cccccc")
ANSWERS = (
    ClosingAnswer(A, AnswerKind.HECHA),
    ClosingAnswer(B, AnswerKind.PARCIAL, Progress(40), Effort(30)),
    ClosingAnswer(C, AnswerKind.NO),
)


@pytest.fixture
def audit() -> MemoryAuditLog:
    return MemoryAuditLog()


@pytest.fixture
def seeded(write_note: WriteNote) -> None:
    write_note("_sistema/horario.md", HORARIO)
    write_note(str(TASKS_NOTE), TASKS)
    write_note(str(MONDAY), monday_note())


def _closing(
    vault: FilesystemVault, audit: MemoryAuditLog, now: datetime = EVENING
) -> DailyClosing:
    clock = FakeClock(now)
    tools = ToolRegistry(vault, samples.effective(), audit, clock)
    return DailyClosing(vault, CoachSession(tools, clock), clock, audit)


def _snapshot(vault: FilesystemVault) -> dict[VaultPath, str]:
    return {path: vault.read(path).text for path in vault.list_notes()}


# --- Vista previa: nada cambia ----------------------------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_preview_changes_nothing_and_audits_nothing(
    vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    before = _snapshot(vault)
    preview = _closing(vault, audit).preview(ANSWERS)
    assert _snapshot(vault) == before
    assert audit.records == []
    by_id = {change.task_id: change for change in preview.changes}
    assert by_id[B].progress_before == Progress(25) or by_id[B].progress_before is None
    assert by_id[B].progress_after == Progress(40)
    assert by_id[B].remaining_after == Effort(30)
    assert by_id[A].kind is AnswerKind.HECHA
    assert [block.task_id for block in preview.missed] == [C]


@pytest.mark.usefixtures("seeded")
def test_preview_without_answers_lists_todays_work_as_unanswered(
    vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    preview = _closing(vault, audit).preview(())
    assert preview.changes == ()
    assert preview.unanswered == (A, B, C)


@pytest.mark.usefixtures("seeded")
def test_answer_for_a_task_outside_today_is_rejected(
    vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    with pytest.raises(ApplicationError, match="t-zzzzzz"):
        _closing(vault, audit).preview((ClosingAnswer(tid("t-zzzzzz"), AnswerKind.HECHA),))


@pytest.mark.usefixtures("seeded")
def test_duplicate_answers_are_rejected(vault: FilesystemVault, audit: MemoryAuditLog) -> None:
    with pytest.raises(ValidationError):
        _closing(vault, audit).preview((ClosingAnswer(A, AnswerKind.HECHA),) * 2)


def test_closing_without_daily_note_is_a_clear_error(
    vault: FilesystemVault, audit: MemoryAuditLog, write_note: WriteNote
) -> None:
    write_note("_sistema/horario.md", HORARIO)
    with pytest.raises(NoDailyPlanError):
        _closing(vault, audit).preview(())


# --- Aplicación confirmada --------------------------------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_apply_updates_tasks_closing_and_proposes_relocation(
    vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    result = _closing(vault, audit).apply(ANSWERS)
    tasks = vault.read(TASKS_NOTE).text.splitlines()
    assert tasks[0].startswith("- [x] Informe de redes")
    assert "[avance:: 40%]" in tasks[1] and "[esfuerzo:: 30m]" in tasks[1]
    assert tasks[2] == TASKS.splitlines()[2]  # "no realizada": la tarea no cambia
    daily = vault.read(MONDAY).text
    closing = daily.split("## Cierre", 1)[1]
    assert "- 21:00 — hecha: t-aaaaaa" in closing
    assert "no realizada: t-cccccc" in closing
    (adjustment,) = parse_adjustments(daily, MON)
    assert adjustment.kind is AdjustmentKind.BLOQUE_PERDIDO
    assert adjustment.task_id == C
    assert [a.task_id for a in result.adjustments] == [C]


@pytest.mark.usefixtures("seeded")
def test_apply_is_audited_as_the_owner(vault: FilesystemVault, audit: MemoryAuditLog) -> None:
    _closing(vault, audit).apply(ANSWERS)
    owner = [(r.action, r.target, r.result) for r in audit.records if r.actor == "propietario"]
    assert ("actualizar_tarea", "t-aaaaaa", AuditResult.INTENTO) in owner
    assert ("actualizar_tarea", "t-aaaaaa", AuditResult.OK) in owner
    assert ("cierre", str(MONDAY), AuditResult.OK) in owner
    coach = [(r.action, r.result) for r in audit.records if r.actor == "coach"]
    assert ("anexar_checkin", AuditResult.OK) in coach


@pytest.mark.usefixtures("seeded")
def test_unanswered_tasks_stay_unchanged_even_when_confirmed(
    vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    before = vault.read(TASKS_NOTE).text
    result = _closing(vault, audit).apply((ClosingAnswer(A, AnswerKind.HECHA),))
    lines = vault.read(TASKS_NOTE).text.splitlines()
    assert lines[1:] == before.splitlines()[1:]
    assert result.preview.unanswered == (B, C)
    assert "sin respuesta: t-bbbbbb (sin cambios)" in vault.read(MONDAY).text


@pytest.mark.usefixtures("seeded")
def test_apply_without_answers_is_rejected(vault: FilesystemVault, audit: MemoryAuditLog) -> None:
    before = _snapshot(vault)
    with pytest.raises(ApplicationError, match="respuesta"):
        _closing(vault, audit).apply(())
    assert _snapshot(vault) == before


@pytest.mark.usefixtures("seeded")
def test_invalid_answer_fails_before_any_write(
    vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    before = _snapshot(vault)
    with pytest.raises(ApplicationError):
        _closing(vault, audit).apply(
            (ClosingAnswer(A, AnswerKind.HECHA), ClosingAnswer(tid("t-zzzzzz"), AnswerKind.NO))
        )
    assert _snapshot(vault) == before
    assert audit.records == []


@pytest.mark.usefixtures("seeded")
def test_failure_midway_reports_what_was_written(
    vault: FilesystemVault, audit: MemoryAuditLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    closing = _closing(vault, audit)
    real_write = vault.write

    def flaky(path: VaultPath, text: str, *, expected: object) -> object:
        if path == MONDAY:
            raise VersionConflictError("editada en Obsidian")
        return real_write(path, text, expected=expected)  # type: ignore[arg-type]

    monkeypatch.setattr(vault, "write", flaky)
    with pytest.raises(ClosingIncompleteError) as raised:
        closing.apply(ANSWERS)
    assert "t-aaaaaa" in raised.value.written
    assert ("cierre", str(MONDAY), AuditResult.ERROR) in [
        (r.action, r.target, r.result) for r in audit.records
    ]
