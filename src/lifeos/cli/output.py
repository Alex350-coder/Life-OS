"""Formato de salida de la CLI: solo presenta resultados de los casos de uso."""

from collections.abc import Iterable

from lifeos.application.jefe import PlanOutcome
from lifeos.application.tasks import LocatedTask
from lifeos.domain.plan import PlanProposal, PlanStatus


def tasks(located: Iterable[LocatedTask]) -> list[str]:
    lines = []
    for item in located:
        task = item.task
        due = f" · vence {task.due}" if task.due else ""
        effort = task.effort.render() if task.effort else "?"
        lines.append(
            f"{task.id or '(sin id)'} · {task.status} · {task.area or '?'} · {effort}{due}"
            f" · {task.title} ({item.path})"
        )
    return lines or ["Sin tareas."]


def _work(proposal: PlanProposal) -> list[str]:
    lines = []
    for block in sorted(proposal.work_blocks(), key=lambda b: b.interval.start.timestamp()):
        start, end = block.interval.start, block.interval.end
        lines.append(f"  {start:%Y-%m-%d %H:%M}–{end:%H:%M}  {block.title} ({block.task_id})")
    return lines or ["  (sin bloques de trabajo)"]


def _details(proposal: PlanProposal) -> list[str]:
    lines = ["Bloques de trabajo:", *_work(proposal)]
    if proposal.unplanned:
        lines.append(f"No caben ({len(proposal.unplanned)}):")
        lines += [
            f"  - {u.title} ({u.task_id or 'sin id'}) · {u.minutes} min · {u.reason}"
            f" → {u.suggestion}"
            for u in proposal.unplanned
        ]
    if proposal.alerts:
        lines.append("Alertas:")
        lines += [f"  - {alert}" for alert in proposal.alerts]
    return lines


def outcome(result: PlanOutcome) -> list[str]:
    week = result.proposal.week
    if not result.accepted:
        return [
            f"Plan de {week} rechazado: {len(result.conflicts)} conflicto(s). No se guardó nada.",
            *(f"  - [{c.code}] {c.message}" for c in result.conflicts),
        ]
    if result.status is PlanStatus.APROBADO:
        return [f"Plan {week} aprobado y publicado en {result.path} y en las notas de Diario/."]
    return [
        f"Plan propuesto para {week} guardado en {result.path} (origen: {result.proposal.origin}).",
        *_details(result.proposal),
        f"Revisa la nota y, si estás de acuerdo, ejecuta: lifeos aprobar --semana {week}",
    ]
