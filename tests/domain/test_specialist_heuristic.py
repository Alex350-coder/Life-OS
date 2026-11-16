"""Heurística determinista de los especialistas (F8-T02, spec agentes-especialistas)."""

from datetime import date

import pytest

from lifeos.domain.area_proposals import MAX_ITEMS, ProposalKind
from lifeos.domain.goals import parse_goals
from lifeos.domain.ids import TaskId
from lifeos.domain.specialist_heuristic import heuristic_items
from lifeos.domain.task import Area, Priority
from lifeos.domain.task_line import TaskLine, parse_task_line

pytestmark = pytest.mark.unit

TODAY = date(2026, 11, 11)


def _line(text: str) -> TaskLine:
    parsed = parse_task_line(text)
    assert parsed is not None
    return parsed


def _task(
    task_id: str, due: str | None, priority: str = "media", extra: str = "", done: bool = False
) -> TaskLine:
    mark = "x" if done else " "
    due_part = f" \U0001f4c5 {due}" if due else ""
    prio = f" [prioridad:: {priority}]" if priority != "media" else ""
    return _line(
        f"- [{mark}] Tarea {task_id} [id:: {task_id}] [area:: universidad]"
        f" [esfuerzo:: 2h]{prio}{extra}{due_part}"
    )


def _goals(text: str = "") -> object:
    return parse_goals(text, Area.UNIVERSIDAD)


def test_overdue_and_imminent_tasks_go_to_high_priority() -> None:
    lines = (
        _task("t-abc222", "2026-11-09"),  # vencida
        _task("t-abc223", "2026-11-13"),  # en 2 días
        _task("t-abc224", "2026-11-30"),  # lejos
        _task("t-abc225", "2026-11-12", priority="alta"),  # ya alta: nada que proponer
    )

    items = heuristic_items(Area.UNIVERSIDAD, lines, parse_goals("", Area.UNIVERSIDAD), TODAY)

    assert [(i.task_id, i.priority) for i in items] == [
        (TaskId("t-abc222"), Priority.ALTA),
        (TaskId("t-abc223"), Priority.ALTA),
    ]
    assert "vencida" in items[0].reason
    assert "2026-11-13" in items[1].reason
    assert all(i.kind is ProposalKind.PRIORIDAD for i in items)


def test_low_priority_with_due_in_ten_days_goes_to_medium() -> None:
    lines = (_task("t-abc226", "2026-11-18", priority="baja"),)

    (item,) = heuristic_items(Area.UNIVERSIDAD, lines, parse_goals("", Area.UNIVERSIDAD), TODAY)

    assert item.priority is Priority.MEDIA


def test_closed_tasks_are_ignored() -> None:
    lines = (_task("t-abc227", "2026-11-12", done=True),)

    assert heuristic_items(Area.UNIVERSIDAD, lines, parse_goals("", Area.UNIVERSIDAD), TODAY) == ()


def test_tasks_without_id_or_due_are_ignored() -> None:
    lines = (
        _line("- [ ] Sin id [area:: universidad] \U0001f4c5 2026-11-12"),
        _task("t-abc228", None),
    )

    assert heuristic_items(Area.UNIVERSIDAD, lines, parse_goals("", Area.UNIVERSIDAD), TODAY) == ()


def test_active_goal_without_open_linked_task_gets_a_next_step() -> None:
    goals = parse_goals(
        "- [meta:: m1] Aprobar Redes [horizonte:: 2026-12-15]\n"
        "- [meta:: m2] Leer un libro [estado:: completada]\n"
        "- [meta:: m3] Practicar inglés\n",
        Area.UNIVERSIDAD,
    )
    lines = (_task("t-abc229", None, extra=" [meta:: m3]"),)

    items = heuristic_items(Area.UNIVERSIDAD, lines, goals, TODAY)

    (item,) = items
    assert item.kind is ProposalKind.NUEVA_TAREA
    assert item.title == "Siguiente paso: Aprobar Redes"
    assert item.due == date(2026, 12, 15)
    assert item.effort is not None and item.effort.minutes == 60
    assert "m1" in item.reason


def test_goal_linked_only_to_closed_tasks_still_needs_a_step() -> None:
    goals = parse_goals("- [meta:: m1] Aprobar Redes\n", Area.UNIVERSIDAD)
    lines = (_task("t-abc22a", None, extra=" [meta:: m1]", done=True),)

    (item,) = heuristic_items(Area.UNIVERSIDAD, lines, goals, TODAY)

    assert item.kind is ProposalKind.NUEVA_TAREA
    assert item.due is None


def test_past_goal_horizon_is_not_used_as_due_date() -> None:
    goals = parse_goals("- [meta:: m1] Algo [horizonte:: 2026-11-01]\n", Area.UNIVERSIDAD)

    (item,) = heuristic_items(Area.UNIVERSIDAD, (), goals, TODAY)

    assert item.due is None
    assert "pasó" in item.reason


def test_keys_are_sequential_and_capped() -> None:
    lines = tuple(
        _task(f"t-abc{n:03d}".replace("0", "2").replace("1", "3"), "2026-11-12") for n in range(30)
    )
    unique = tuple({line.task.id: line for line in lines}.values())

    items = heuristic_items(Area.UNIVERSIDAD, unique, parse_goals("", Area.UNIVERSIDAD), TODAY)

    assert len(items) == min(len(unique), MAX_ITEMS)
    assert [i.key for i in items] == [f"p{n}" for n in range(1, len(items) + 1)]


def test_tasks_from_other_areas_are_ignored() -> None:
    other = _line(
        "- [ ] Web [id:: t-abc22b] [area:: proyectos] [esfuerzo:: 1h] \U0001f4c5 2026-11-12"
    )

    assert (
        heuristic_items(Area.UNIVERSIDAD, (other,), parse_goals("", Area.UNIVERSIDAD), TODAY) == ()
    )
