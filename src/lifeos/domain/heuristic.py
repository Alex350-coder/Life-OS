"""Heurística determinista de propuesta semanal (F3-T01, F3-T04; planificador.md).

Ordena por dependencias, fecha límite y prioridad, y coloca bloques solo en el tiempo libre y
dentro del presupuesto diario: nunca toca compromisos fijos, tiempo protegido ni el tiempo
libre mínimo. Lo que no cabe se informa con motivo y propuesta de aplazamiento. La propuesta
aún debe pasar el validador.
"""

import heapq
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from lifeos.domain.capacity import Interval
from lifeos.domain.ids import TaskId
from lifeos.domain.plan import (
    PLANNABLE,
    Block,
    BlockKind,
    PlanningContext,
    PlanOrigin,
    PlanProposal,
    Unplanned,
    UnplannedCode,
    fixed_and_protected_blocks,
)
from lifeos.domain.task import Priority, Task, TaskStatus

_PRIORITY_RANK = {Priority.ALTA: 0, Priority.MEDIA: 1, Priority.BAJA: 2}


def _today(context: PlanningContext) -> date:
    return context.not_before.astimezone(context.zone).date()


def _effort(task: Task) -> int:
    return task.effort.minutes if task.effort is not None else 0


def _sort_key(task: Task) -> tuple[date, int, str]:
    due = task.due or date.max
    return due, _PRIORITY_RANK[task.priority], task.id.value if task.id else ""


@dataclass
class _Outcome:
    """Resultado mutable mientras se construye la propuesta (no sale de este módulo)."""

    work: list[Block] = field(default_factory=list)
    unplanned: list[Unplanned] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    finished: dict[TaskId, datetime] = field(default_factory=dict)  # tarea entera → último fin


# --- Candidatas y orden -------------------------------------------------------------------------


def _missing(task: Task) -> str | None:
    fields = task.missing_required_fields()
    return f"falta información crítica: {', '.join(fields)}" if fields else None


def _candidates(context: PlanningContext, out: _Outcome) -> dict[TaskId, Task]:
    today = _today(context)
    candidates: dict[TaskId, Task] = {}
    for task in context.incomplete:
        out.unplanned.append(
            Unplanned(
                None,
                task.title,
                _effort(task),
                UnplannedCode.SIN_ID,
                "sin id: no se puede citar en el plan",
                "Asigna un id a la tarea (formato-de-tareas) y vuelve a planificar",
            )
        )
    for task_id, task in sorted(context.tasks.items(), key=lambda item: item[0].value):
        if task.status not in PLANNABLE:
            continue
        missing = _missing(task)
        if missing is not None:
            out.unplanned.append(
                Unplanned(
                    task_id,
                    task.title,
                    _effort(task),
                    UnplannedCode.FALTA_INFORMACION,
                    missing,
                    "Completa los campos de la tarea y vuelve a planificar",
                )
            )
            continue
        if task.inferred:
            out.alerts.append(f"'{task.title}' ({task_id}): esfuerzo inferido, confírmalo")
        if task.due is not None and task.due < today:
            out.alerts.append(f"'{task.title}' ({task_id}): fecha límite vencida el {task.due}")
        candidates[task_id] = task
    return candidates


def _ordered(candidates: dict[TaskId, Task], out: _Outcome) -> list[Task]:
    """Orden topológico estable: entre las tareas listas, primero la más urgente."""
    waiting = {
        task_id: {dep for dep in task.depends_on if dep in candidates}
        for task_id, task in candidates.items()
    }
    ready = [
        (_sort_key(task), task_id.value)
        for task_id, task in candidates.items()
        if not waiting[task_id]
    ]
    heapq.heapify(ready)
    by_value = {task_id.value: task_id for task_id in candidates}
    order: list[Task] = []
    while ready:
        _, value = heapq.heappop(ready)
        done = by_value[value]
        order.append(candidates[done])
        for task_id, deps in waiting.items():
            if done in deps:
                deps.discard(done)
                if not deps:
                    heapq.heappush(ready, (_sort_key(candidates[task_id]), task_id.value))
    placed = {task.id for task in order}
    for task_id in sorted(set(candidates) - placed, key=lambda t: t.value):
        task = candidates[task_id]
        out.unplanned.append(
            Unplanned(
                task_id,
                task.title,
                _effort(task),
                UnplannedCode.DEPENDENCIA_CIRCULAR,
                "dependencia circular entre tareas",
                "Revisa los campos [depende::] de las tareas implicadas",
            )
        )
    return order


# --- Colocación -------------------------------------------------------------------------------


@dataclass
class _Day:
    day: date
    budget: int
    slots: list[tuple[datetime, datetime]]  # huecos libres en UTC, ordenados


def _days(context: PlanningContext) -> list[_Day]:
    return [
        _Day(
            day=capacity.day,
            budget=capacity.budget_minutes,
            slots=[(i.start.astimezone(UTC), i.end.astimezone(UTC)) for i in capacity.free],
        )
        for capacity in context.days
    ]


def _allowed_days(task: Task, days: list[_Day], today: date) -> list[_Day]:
    """Antes de la fecha límite primero; el propio día solo si hace falta; nunca después."""
    if task.due is None or task.due < today:
        return days
    return [d for d in days if d.day < task.due] + [d for d in days if d.day == task.due]


def _minutes(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds()) // 60


def _chunk(remaining: int, room: int, context: PlanningContext, divisible: bool) -> int:
    limits = context.limits
    if not divisible:
        return remaining if room >= remaining else 0
    chunk = min(remaining, room, limits.block_max)
    if 0 < remaining - chunk < limits.block_min:
        chunk = remaining - limits.block_min  # no dejar un resto menor que el bloque mínimo
    if chunk < limits.block_min and chunk != remaining:
        return 0
    return max(chunk, 0)


def _take(
    day: _Day, task: Task, remaining: int, earliest: datetime, context: PlanningContext
) -> tuple[list[Block], int]:
    blocks = []
    for index in range(len(day.slots)):
        # Un mismo hueco puede recibir varios bloques seguidos (cada uno ≤ bloque máximo).
        while remaining > 0 and day.budget > 0:
            start, end = day.slots[index]
            begin = max(start, earliest)
            if begin >= end:
                break
            room = min(_minutes(begin, end), day.budget)
            chunk = _chunk(remaining, room, context, task.divisible)
            if chunk == 0:
                break
            stop = begin + timedelta(minutes=chunk)
            interval = Interval(begin.astimezone(context.zone), stop.astimezone(context.zone))
            blocks.append(Block(BlockKind.TRABAJO, task.title, interval, task.id))
            day.slots[index] = (stop, end)
            day.budget -= chunk
            remaining -= chunk
    return blocks, remaining


def _blocking_problem(
    task: Task, context: PlanningContext, out: _Outcome
) -> tuple[UnplannedCode, str] | None:
    """Motivo por el que la tarea no se puede colocar en absoluto, si lo hay."""
    for dep_id in task.depends_on:
        dep = context.tasks.get(dep_id)
        if dep is None:
            return UnplannedCode.DEPENDENCIA, f"dependencia desconocida {dep_id}"
        if dep.status is not TaskStatus.COMPLETADA and dep_id not in out.finished:
            return (
                UnplannedCode.DEPENDENCIA,
                f"la dependencia {dep_id} no queda planificada entera esta semana",
            )
    if not task.divisible and _effort(task) > context.limits.block_max:
        return (
            UnplannedCode.NO_DIVISIBLE,
            f"no divisible y mayor que el bloque máximo ({context.limits.block_max} min)",
        )
    return None


def _suggestion(task: Task, context: PlanningContext) -> str:
    week_days = context.week.days()
    if task.due is not None and task.due <= week_days[-1]:
        return f"Renegociar la fecha límite ({task.due}) o reducir el alcance"
    return f"Aplazar a la semana {context.week.next()}"


def _place(
    task_id: TaskId, task: Task, days: list[_Day], context: PlanningContext, out: _Outcome
) -> None:
    effort = _effort(task)
    problem = _blocking_problem(task, context, out)
    if problem is not None:
        code, reason = problem
        out.unplanned.append(
            Unplanned(task_id, task.title, effort, code, reason, _suggestion(task, context))
        )
        return
    earliest = max(
        [context.not_before.astimezone(UTC)]
        + [out.finished[d] for d in task.depends_on if d in out.finished]
    )
    remaining = effort
    placed: list[Block] = []
    for day in _allowed_days(task, days, _today(context)):
        blocks, remaining = _take(day, task, remaining, earliest, context)
        placed += blocks
        if remaining == 0:
            break
    out.work += placed
    if remaining == 0:
        out.finished[task_id] = max(b.interval.end.astimezone(UTC) for b in placed)
        return
    code, reason = (
        (UnplannedCode.NO_CABE_ENTERA, f"no cabe entera: quedan {remaining} min sin hueco")
        if placed
        else (UnplannedCode.SIN_CAPACIDAD, "no hay hueco ni capacidad suficiente en la semana")
    )
    out.unplanned.append(
        Unplanned(task_id, task.title, remaining, code, reason, _suggestion(task, context))
    )


def _justification(out: _Outcome) -> str:
    planned = len({block.task_id for block in out.work})
    return (
        "Propuesta de la heurística determinista: ordena por dependencias, fecha límite y "
        f"prioridad; {planned} tareas con bloques y {len(out.unplanned)} sin planificar del todo."
    )


def _sorted(blocks: Iterable[Block]) -> tuple[Block, ...]:
    return tuple(sorted(blocks, key=lambda b: b.interval.start.timestamp()))


def propose(context: PlanningContext) -> PlanProposal:
    out = _Outcome()
    candidates = _candidates(context, out)
    days = _days(context)
    for task in _ordered(candidates, out):
        if task.id is not None:  # las candidatas siempre tienen id
            _place(task.id, task, days, context, out)
    return PlanProposal(
        week=context.week,
        blocks=(*fixed_and_protected_blocks(context), *_sorted(out.work)),
        unplanned=tuple(out.unplanned),
        justification=_justification(out),
        origin=PlanOrigin.HEURISTICA,
        alerts=tuple(out.alerts),
    )


def plannable_tasks(context: PlanningContext) -> tuple[Task, ...]:
    """Tareas que se pueden programar (con id, área y esfuerzo), ordenadas por id."""
    return tuple(_candidates(context, _Outcome()).values())


def unplanned_after(
    context: PlanningContext, work: Iterable[Block]
) -> tuple[tuple[Unplanned, ...], tuple[str, ...]]:
    """Lo que una propuesta ajena (p. ej., del modelo) deja sin planificar, más las alertas."""
    out = _Outcome()
    candidates = _candidates(context, out)
    planned: dict[TaskId, int] = {}
    for block in work:
        if block.task_id is not None:
            planned[block.task_id] = planned.get(block.task_id, 0) + block.interval.minutes
    for task_id, task in candidates.items():
        remaining = _effort(task) - planned.get(task_id, 0)
        if remaining <= 0:
            continue
        code = UnplannedCode.NO_CABE_ENTERA if task_id in planned else UnplannedCode.SIN_CAPACIDAD
        out.unplanned.append(
            Unplanned(
                task_id,
                task.title,
                remaining,
                code,
                f"la propuesta no le asigna {remaining} min",
                _suggestion(task, context),
            )
        )
    return tuple(out.unplanned), tuple(out.alerts)
