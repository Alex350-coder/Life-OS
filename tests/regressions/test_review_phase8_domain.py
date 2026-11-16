"""Regresiones de la revisión de la Fase 8 en el dominio (python-reviewer, type-design-analyzer,
security-reviewer y silent-failure-hunter)."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from lifeos.domain.agent_lifecycle import (
    AgentState,
    LifecycleAction,
    LifecycleError,
    RegistryEntry,
    apply_action,
    register_import,
)
from lifeos.domain.area_proposals import (
    AreaProposal,
    ProposalItem,
    ProposalKind,
    ProposalRef,
    ProposalStatus,
    parse_proposal_note,
    render_proposal,
    with_status,
)
from lifeos.domain.errors import ValidationError
from lifeos.domain.goals import parse_goals
from lifeos.domain.ids import TaskId
from lifeos.domain.task import Area, Priority
from lifeos.domain.weeks import IsoWeek

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def _note() -> str:
    item = ProposalItem(
        "p1", ProposalKind.PRIORIDAD, Priority.ALTA, "vence", task_id=TaskId("t-abc234")
    )
    proposal = AreaProposal(
        "universidad", Area.UNIVERSIDAD, IsoWeek.parse("2026-W46"), "heuristica", (item,)
    )
    return render_proposal(proposal)


@pytest.mark.parametrize("separator", ["\u2028", "\x0c", "\x85", "\x1c"])
def test_with_status_marks_the_right_line_despite_unicode_separators(separator: str) -> None:
    # Antes: splitlines() y split("\n") contaban líneas distintas y se pisaba otra línea.
    text = _note().replace("> Estas", f"nota{separator}del propietario\n> Estas") + "línea final\n"

    updated = with_status(text, "p1", ProposalStatus.ACEPTADA)

    assert "línea final" in updated
    assert f"nota{separator}del propietario" in updated
    assert parse_proposal_note(updated)[0].status is ProposalStatus.ACEPTADA


def test_with_status_keeps_crlf_line_endings() -> None:
    text = _note().replace("\n", "\r\n")

    updated = with_status(text, "p1", ProposalStatus.RECHAZADA)

    assert updated.count("\r\n") == text.count("\r\n")
    assert parse_proposal_note(updated)[0].status is ProposalStatus.RECHAZADA


def test_backticks_in_free_text_are_neutralized() -> None:
    item = ProposalItem(
        "p1",
        ProposalKind.PRIORIDAD,
        Priority.ALTA,
        "`$= dv.io.load('x')` motivo",
        task_id=TaskId("t-abc234"),
    )

    assert "`" not in item.reason


@pytest.mark.parametrize(("note", "key"), [("Mis notas", "p1"), ("universidad", "x1")])
def test_proposal_ref_validates_on_construction(note: str, key: str) -> None:
    with pytest.raises(ValidationError):
        ProposalRef(IsoWeek.parse("2026-W46"), note, key)


def test_goal_field_after_text_is_counted_as_problem() -> None:
    # Una tarea con casilla puede enlazar una meta: esa no cuenta como problema.
    report = parse_goals("- Texto suelto [meta:: m1]\n- [ ] [meta:: m2] casilla\n", Area.HOBBIES)

    assert report.goals == ()
    assert report.problems == 1


def _entry() -> RegistryEntry:
    entry = register_import(None, "universidad", "a" * 64, 1, T0)
    return apply_action(entry, LifecycleAction.PROBAR, T0)


@pytest.mark.parametrize("current", [0, 2, -1])
def test_registry_entry_rejects_current_out_of_range(current: int) -> None:
    with pytest.raises(LifecycleError, match="versión actual"):
        replace(_entry(), current=current)


def test_registry_entry_state_must_match_last_transition() -> None:
    with pytest.raises(LifecycleError, match="historial"):
        replace(_entry(), state=AgentState.ACTIVO)


def test_registry_entry_history_versions_must_exist() -> None:
    entry = _entry()
    bad = replace(entry.history[-1], version=5)

    with pytest.raises(LifecycleError, match="historial"):
        replace(entry, history=(*entry.history[:-1], bad))


def test_registry_entry_needs_history() -> None:
    with pytest.raises(LifecycleError, match="historial"):
        replace(_entry(), history=())
