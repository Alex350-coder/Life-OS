"""Formato de salida de la CLI: solo presenta resultados de los casos de uso."""

from collections.abc import Iterable
from zoneinfo import ZoneInfo

from lifeos.application.closing import ClosingPreview, ClosingResult
from lifeos.application.coach import Briefing, CheckInOutcome
from lifeos.application.jefe import PlanOutcome
from lifeos.application.tasks import LocatedTask
from lifeos.application.usage_report import UsageRow, UsageSummary
from lifeos.domain.adjustments import adjustment_entry
from lifeos.domain.closing import AnswerKind, TaskChange
from lifeos.domain.coach import block_span
from lifeos.domain.plan import Block, BlockKind, PlanProposal, PlanStatus


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


# --- Ciclo diario (F4-T05) --------------------------------------------------------------------


def _span(block: Block, zone: ZoneInfo) -> str:
    return block_span(block, zone).render()


def _block(block: Block, zone: ZoneInfo) -> str:
    task = f" ({block.task_id})" if block.task_id else ""
    return f"{_span(block, zone)} {block.title}{task}"


def briefing(day: Briefing) -> list[str]:
    zone = day.zone
    state = "plan aprobado" if day.approved else "plan sin aprobar"
    lines = [f"Hoy {day.day} · {state}"]
    if not day.readable:
        lines.append("Aviso: la nota de hoy no sigue el formato del plan; revisa sus bloques.")
    if day.resting is not None:
        lines.append(f"Tiempo protegido ahora: {_block(day.resting, zone)}. Descansa sin culpa.")
    lines.append(f"Ahora: {_block(day.current, zone)}" if day.current else "Ahora: sin bloque")
    if day.upcoming is not None:
        lines.append(f"Siguiente: {_block(day.upcoming, zone)}")
    lines.append("Bloques de trabajo:")
    lines += [f"  {_block(b, zone)}" for b in day.work] or ["  (ninguno)"]
    protected = [b for b in day.blocks if b.kind is BlockKind.PROTEGIDO]
    if protected:
        lines.append("Tiempo protegido: " + ", ".join(_block(b, zone) for b in protected))
    lines.append(f"Check-ins: {day.checkins}")
    if day.adjustments:
        lines.append(
            f"Propuestas para el Jefe: {len(day.adjustments)} → revísalas con "
            "'lifeos semana --replanificar'"
        )
    return lines


def checkin(result: CheckInOutcome) -> list[str]:
    lines = [result.message]
    if result.micro_step is not None:
        lines.append(f"Micro-paso ({result.micro_step.minutes} min): {result.micro_step.action}")
    if result.adjustment is not None:
        lines.append(
            "Propuesta para el Jefe registrada: "
            f"{adjustment_entry(result.adjustment).text}. "
            "Revísala con 'lifeos semana --replanificar'."
        )
    if result.capture is not None:
        lines.append(f"Captura guardada en {result.capture}")
    if result.ai_fallback:
        lines.append("(El modelo no respondió bien; se usó la respuesta sin IA.)")
    lines.append(f"Registrado en {result.path}")
    return lines


def _change(change: TaskChange) -> str:
    if change.kind is AnswerKind.HECHA:
        return f"  {change.task_id} · {change.title} → completada"
    if change.kind is AnswerKind.NO:
        return f"  {change.task_id} · {change.title} → no realizada: propuesta de reubicación"
    parts = []
    if change.progress_after is not None:
        before = change.progress_before.render() if change.progress_before else "?"
        parts.append(f"avance {before} → {change.progress_after.render()}")
    if change.remaining_after is not None:
        before = change.remaining_before.render() if change.remaining_before else "?"
        parts.append(f"restante {before} → {change.remaining_after.render()}")
    return f"  {change.task_id} · {change.title} → " + ", ".join(parts)


def _closing_lines(preview: ClosingPreview) -> list[str]:
    lines = [f"Cierre de {preview.day}:"]
    lines += [_change(change) for change in preview.changes] or ["  (sin respuestas)"]
    if preview.unanswered:
        lines.append("Sin respuesta (no cambian): " + ", ".join(map(str, preview.unanswered)))
    return lines


def closing_preview(preview: ClosingPreview) -> list[str]:
    return [
        *_closing_lines(preview),
        "No se modificó nada. Repite la orden con --confirmar para aplicarlo.",
    ]


def closing_result(result: ClosingResult) -> list[str]:
    lines = [*_closing_lines(result.preview), "Aplicado: " + ", ".join(result.written)]
    if result.adjustments:
        lines.append(
            f"Propuestas de reubicación para el Jefe: {len(result.adjustments)} "
            "(lifeos semana --replanificar)"
        )
    return lines


def _cost(row: UsageRow) -> str:
    known = f"{row.cost_estimated} USD"
    return f"{known} + {row.cost_unknown} desconocido(s)" if row.cost_unknown else known


def _usage_row(row: UsageRow) -> str:
    unknown = f" (+{row.tokens_unknown} sin dato)" if row.tokens_unknown else ""
    return (
        f"  {row.agent} · {row.ritual}: {row.calls} llamadas, {row.attempts} intentos, "
        f"{row.errors} errores, tokens {row.tokens_prompt}/{row.tokens_completion}{unknown}, "
        f"costo {_cost(row)}"
    )


def usage(summary: UsageSummary, month: str) -> list[str]:
    lines = [f"Uso de modelos en {month} (UTC):"]
    if not summary.rows:
        lines.append("  Sin llamadas registradas.")
    else:
        lines += [_usage_row(row) for row in summary.rows]
        lines.append(_usage_row(summary.total))
    if summary.malformed:
        lines.append(f"Aviso: {summary.malformed} línea(s) del registro no se pudieron leer.")
    return lines
