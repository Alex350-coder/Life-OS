"""Heurística determinista de un especialista (F8-T02, spec agentes-especialistas).

Funciona sin modelo y sirve de base cuando la IA no está disponible. Reglas transparentes:
1. Tarea abierta del área vencida o que vence en `URGENT_DAYS` días y no es alta → alta.
2. Tarea abierta en prioridad baja que vence en `SOON_DAYS` días → media.
3. Meta activa sin ninguna tarea abierta enlazada (`[meta:: mN]`) → tarea nueva «Siguiente
   paso: …» de `NEXT_STEP_EFFORT`, con la fecha del horizonte si aún no pasó.

Solo propone: nada cambia hasta que el propietario acepte (RULES C.3).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from lifeos.domain.area_proposals import MAX_ITEMS, ProposalItem, ProposalKind
from lifeos.domain.goals import Goal, GoalsReport
from lifeos.domain.task import Area, Effort, Priority, TaskStatus
from lifeos.domain.task_line import TaskLine

URGENT_DAYS = 3
SOON_DAYS = 10
NEXT_STEP_EFFORT = "1h"
GOAL_FIELD = "meta"
_CLOSED = frozenset({TaskStatus.COMPLETADA, TaskStatus.CANCELADA})


@dataclass(frozen=True, slots=True)
class _Draft:
    sort_key: tuple[int, date]
    kind: ProposalKind
    priority: Priority
    reason: str
    line: TaskLine | None = None
    goal: Goal | None = None
    due: date | None = None


def _is_open(line: TaskLine, area: Area) -> bool:
    task = line.task
    return task.area is area and task.status not in _CLOSED


def _priority_change(line: TaskLine, today: date) -> _Draft | None:
    task = line.task
    if task.id is None or task.due is None:
        return None
    days = (task.due - today).days
    when = "está vencida" if days < 0 else f"vence el {task.due.isoformat()} (en {days} días)"
    if days <= URGENT_DAYS and task.priority is not Priority.ALTA:
        return _Draft((0, task.due), ProposalKind.PRIORIDAD, Priority.ALTA, when, line=line)
    if days <= SOON_DAYS and task.priority is Priority.BAJA:
        return _Draft((1, task.due), ProposalKind.PRIORIDAD, Priority.MEDIA, when, line=line)
    return None


def _linked_goals(lines: Sequence[TaskLine]) -> frozenset[str]:
    return frozenset(
        value for line in lines for key, value in line.extra_fields if key == GOAL_FIELD
    )


def _goal_step(goal: Goal, today: date) -> _Draft:
    reason = f"la meta {goal.key} no tiene tareas abiertas"
    due = None
    if goal.horizon is not None and goal.horizon >= today:
        due = goal.horizon
        reason += f" y su horizonte es {goal.horizon.isoformat()}"
    elif goal.horizon is not None:
        reason += f"; su horizonte ({goal.horizon.isoformat()}) ya pasó: revísalo"
    return _Draft(
        (2, due or date.max), ProposalKind.NUEVA_TAREA, Priority.MEDIA, reason, goal=goal, due=due
    )


def _item(key: str, draft: _Draft) -> ProposalItem:
    if draft.kind is ProposalKind.PRIORIDAD:
        task_id = draft.line.task.id if draft.line is not None else None
        return ProposalItem(key, draft.kind, draft.priority, draft.reason, task_id=task_id)
    return ProposalItem(
        key,
        draft.kind,
        draft.priority,
        draft.reason,
        title=f"Siguiente paso: {draft.goal.title}" if draft.goal is not None else None,
        effort=Effort.parse(NEXT_STEP_EFFORT),
        due=draft.due,
    )


def heuristic_items(
    area: Area, lines: Sequence[TaskLine], goals: GoalsReport, today: date
) -> tuple[ProposalItem, ...]:
    open_lines = [line for line in lines if _is_open(line, area)]
    drafts = [d for line in open_lines if (d := _priority_change(line, today)) is not None]
    linked = _linked_goals(open_lines)
    drafts += [_goal_step(goal, today) for goal in goals.open_goals if goal.key not in linked]
    ordered = sorted(drafts, key=lambda draft: draft.sort_key)[:MAX_ITEMS]
    return tuple(_item(f"p{n}", draft) for n, draft in enumerate(ordered, start=1))
