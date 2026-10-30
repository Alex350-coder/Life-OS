"""Validador determinista de planes (F3-T02, planificador.md, SEC-09).

Este paso no se puede omitir: ninguna propuesta, venga de la heurística o del modelo, se guarda
sin pasar por aquí. Devuelve todos los conflictos con un motivo legible; una tupla vacía
significa plan viable.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta

from lifeos.domain.capacity import DayCapacity, Interval
from lifeos.domain.ids import TaskId
from lifeos.domain.plan import (
    PLANNABLE,
    Block,
    BlockKind,
    Conflict,
    ConflictCode,
    PlanningContext,
    PlanProposal,
    fixed_and_protected_blocks,
)
from lifeos.domain.task import Task, TaskStatus


def _hhmm(block: Block, context: PlanningContext) -> str:
    start = block.interval.start.astimezone(context.zone)
    end = block.interval.end.astimezone(context.zone)
    return f"{start:%Y-%m-%d %H:%M}–{end:%H:%M}"


def _label(block: Block, context: PlanningContext) -> str:
    return f"'{block.title}' ({_hhmm(block, context)})"


def _day(block: Block, context: PlanningContext) -> date:
    return context.local_day(block.interval.start)


# --- Estructura del plan ------------------------------------------------------------------------


def _check_calendar(block: Block, context: PlanningContext) -> list[Conflict]:
    day = _day(block, context)
    conflicts = []
    if day not in context.week.days():
        conflicts.append(
            Conflict(
                ConflictCode.FUERA_DE_SEMANA,
                f"{_label(block, context)} no pertenece a la semana {context.week}",
                day,
            )
        )
    end = block.interval.end.astimezone(context.zone)
    ends_at_midnight = (
        end.date() == day + timedelta(days=1) and end.time().isoformat() == "00:00:00"
    )
    if end.date() != day and not ends_at_midnight:
        conflicts.append(
            Conflict(ConflictCode.CRUZA_DIA, f"{_label(block, context)} cruza la medianoche", day)
        )
    return conflicts


def _check_overlaps(blocks: Iterable[Block], context: PlanningContext) -> list[Conflict]:
    ordered = sorted(
        blocks, key=lambda b: (b.interval.start.timestamp(), b.interval.end.timestamp())
    )
    conflicts = []
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            if second.interval.start.timestamp() >= first.interval.end.timestamp():
                break
            conflicts.append(
                Conflict(
                    ConflictCode.SOLAPAMIENTO,
                    f"{_label(first, context)} se solapa con {_label(second, context)}",
                    _day(first, context),
                )
            )
    return conflicts


def _signature(block: Block) -> tuple[BlockKind, str, float, float]:
    return (
        block.kind,
        block.title,
        block.interval.start.timestamp(),
        block.interval.end.timestamp(),
    )


def _check_schedule_blocks(proposal: PlanProposal, context: PlanningContext) -> list[Conflict]:
    """Los fijos y el tiempo protegido del horario deben estar exactamente, sin añadidos."""
    expected = {_signature(block): block for block in fixed_and_protected_blocks(context)}
    present = {
        _signature(block): block for block in proposal.blocks if block.kind is not BlockKind.TRABAJO
    }
    conflicts = []
    for key in expected.keys() - present.keys():
        block = expected[key]
        code = (
            ConflictCode.FIJO_ALTERADO
            if block.kind is BlockKind.FIJO
            else ConflictCode.PROTEGIDO_ELIMINADO
        )
        conflicts.append(
            Conflict(code, f"falta {_label(block, context)} o fue movido", _day(block, context))
        )
    for key in present.keys() - expected.keys():
        block = present[key]
        code = (
            ConflictCode.FIJO_ALTERADO
            if block.kind is BlockKind.FIJO
            else ConflictCode.PROTEGIDO_ELIMINADO
        )
        conflicts.append(
            Conflict(
                code,
                f"{_label(block, context)} no está en el horario (movido o inventado)",
                _day(block, context),
            )
        )
    return sorted(conflicts, key=lambda c: (c.day or date.min, c.message))


# --- Bloques de trabajo -------------------------------------------------------------------------


def _blocking_conflict(
    block: Block, capacity: DayCapacity, context: PlanningContext
) -> Conflict | None:
    day = capacity.day
    for event in capacity.protected:
        if block.interval.overlaps(event.interval):
            return Conflict(
                ConflictCode.TIEMPO_PROTEGIDO,
                f"{_label(block, context)} invade el tiempo protegido '{event.title}'",
                day,
            )
    for event in capacity.fixed:
        if block.interval.overlaps(event.interval):
            return Conflict(
                ConflictCode.COMPROMISO_FIJO,
                f"{_label(block, context)} pisa el compromiso fijo '{event.title}'",
                day,
            )
    return None


def _within(interval: Interval, windows: Iterable[Interval]) -> bool:
    return any(window.contains(interval) for window in windows)


def _check_placement(block: Block, context: PlanningContext) -> list[Conflict]:
    day = _day(block, context)
    if block.interval.start.timestamp() < context.not_before.timestamp():
        return [
            Conflict(
                ConflictCode.FECHA_PASADA,
                f"{_label(block, context)} empieza antes de {context.not_before:%Y-%m-%d %H:%M}",
                day,
            )
        ]
    capacity = context.day(day)
    if capacity is None:
        return []  # ya informado como fuera de semana
    blocking = _blocking_conflict(block, capacity, context)
    if blocking is not None:
        return [blocking]
    if not _within(block.interval, capacity.free):
        return [
            Conflict(
                ConflictCode.FUERA_DE_DISPONIBILIDAD,
                f"{_label(block, context)} está fuera de la disponibilidad declarada",
                day,
            )
        ]
    return []


def _check_load(work: Iterable[Block], context: PlanningContext) -> list[Conflict]:
    per_day: dict[date, int] = defaultdict(int)
    for block in work:
        per_day[_day(block, context)] += block.interval.minutes
    conflicts = []
    for day, minutes in sorted(per_day.items()):
        capacity = context.day(day)
        allowed = capacity.budget_minutes if capacity is not None else 0
        if minutes > allowed:
            conflicts.append(
                Conflict(
                    ConflictCode.EXCESO_DE_CARGA,
                    f"{day}: {minutes} min de trabajo superan la capacidad de {allowed} min "
                    "(foco máximo, margen y tiempo libre mínimo)",
                    day,
                )
            )
    return conflicts


# --- Tareas -------------------------------------------------------------------------------------


def _effort(task: Task) -> int:
    return task.effort.minutes if task.effort is not None else 0


def _check_task_blocks(task: Task, blocks: list[Block], context: PlanningContext) -> list[Conflict]:
    limits = context.limits
    effort = _effort(task)
    total = sum(block.interval.minutes for block in blocks)
    name = f"'{task.title}' ({task.id})"
    conflicts = []
    if total > effort:
        conflicts.append(
            Conflict(
                ConflictCode.EXCEDE_ESFUERZO,
                f"{name}: {total} min planificados superan el esfuerzo restante ({effort} min)",
            )
        )
    if not task.divisible and (len(blocks) > 1 or total != effort):
        conflicts.append(
            Conflict(
                ConflictCode.NO_DIVISIBLE_PARTIDA,
                f"{name} no es divisible: necesita un único bloque de {effort} min",
            )
        )
    for block in blocks:
        minutes = block.interval.minutes
        if minutes > limits.block_max:
            conflicts.append(
                Conflict(
                    ConflictCode.BLOQUE_LARGO,
                    f"{_label(block, context)} supera el bloque máximo ({limits.block_max} min)",
                    _day(block, context),
                )
            )
        if minutes < limits.block_min and minutes != effort:
            conflicts.append(
                Conflict(
                    ConflictCode.BLOQUE_CORTO,
                    f"{_label(block, context)} es menor que el bloque mínimo "
                    f"({limits.block_min} min)",
                    _day(block, context),
                )
            )
        conflicts += _check_due(task, block, context)
    return conflicts


def _check_due(task: Task, block: Block, context: PlanningContext) -> list[Conflict]:
    day = _day(block, context)
    # Una tarea ya vencida se puede recuperar; lo que no se permite es pasar una fecha futura.
    if task.due is None or task.due < context.not_before.astimezone(context.zone).date():
        return []
    if day > task.due:
        return [
            Conflict(
                ConflictCode.DESPUES_DE_FECHA_LIMITE,
                f"{_label(block, context)} queda después de la fecha límite {task.due}",
                day,
            )
        ]
    return []


def _check_dependencies(
    task: Task, own: list[Block], by_task: dict[TaskId, list[Block]], context: PlanningContext
) -> list[Conflict]:
    conflicts = []
    first_start = min(b.interval.start.timestamp() for b in own)
    for dep_id in task.depends_on:
        dep = context.tasks.get(dep_id)
        if dep is not None and dep.status is TaskStatus.COMPLETADA:
            continue
        dep_blocks = by_task.get(dep_id, [])
        planned = sum(block.interval.minutes for block in dep_blocks)
        if dep is None or not dep_blocks or planned < _effort(dep):
            reason = "no está completada ni planificada entera esta semana"
        elif max(b.interval.end.timestamp() for b in dep_blocks) > first_start:
            reason = "termina después de que empiece la tarea dependiente"
        else:
            continue
        conflicts.append(
            Conflict(
                ConflictCode.DEPENDENCIA,
                f"'{task.title}' ({task.id}) depende de {dep_id}, que {reason}",
            )
        )
    return conflicts


def _check_tasks(work: Iterable[Block], context: PlanningContext) -> list[Conflict]:
    by_task: dict[TaskId, list[Block]] = defaultdict(list)
    for block in work:
        if block.task_id is not None:  # siempre en bloques de trabajo (Block lo exige)
            by_task[block.task_id].append(block)
    conflicts = []
    for task_id, blocks in sorted(by_task.items(), key=lambda item: item[0].value):
        task = context.tasks.get(task_id)
        if task is None:
            conflicts.append(
                Conflict(ConflictCode.TAREA_DESCONOCIDA, f"{task_id} no es una tarea de la bóveda")
            )
            continue
        if task.status not in PLANNABLE:
            conflicts.append(
                Conflict(
                    ConflictCode.TAREA_NO_PLANIFICABLE,
                    f"'{task.title}' ({task_id}) está {task.status} y no se puede programar",
                )
            )
            continue
        conflicts += _check_task_blocks(task, blocks, context)
        conflicts += _check_dependencies(task, blocks, by_task, context)
    return conflicts


def validate_plan(proposal: PlanProposal, context: PlanningContext) -> tuple[Conflict, ...]:
    """Todos los conflictos del plan frente al horario, la capacidad y las tareas."""
    if proposal.week != context.week:
        return (
            Conflict(
                ConflictCode.FUERA_DE_SEMANA,
                f"el plan es de {proposal.week} pero el contexto es de {context.week}",
            ),
        )
    work = proposal.work_blocks()
    conflicts: list[Conflict] = []
    for block in proposal.blocks:
        conflicts += _check_calendar(block, context)
    conflicts += _check_overlaps(proposal.blocks, context)
    conflicts += _check_schedule_blocks(proposal, context)
    for block in work:
        conflicts += _check_placement(block, context)
    conflicts += _check_load(work, context)
    conflicts += _check_tasks(work, context)
    return tuple(conflicts)
