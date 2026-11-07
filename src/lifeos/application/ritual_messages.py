"""Textos de los rituales para el móvil: breves, sin culpa y en texto plano (ADR-0014).

Los títulos de bloques y tareas son datos de la bóveda: se muestran tal cual, nunca se ejecutan.
"""

from datetime import date, tzinfo

from lifeos.application.coach import Briefing
from lifeos.application.jefe import PlanOutcome, WeekView
from lifeos.domain.plan import Block, BlockKind
from lifeos.domain.weeks import IsoWeek


def _span(block: Block, zone: tzinfo | None) -> str:
    start = block.interval.start.astimezone(zone)
    end = block.interval.end.astimezone(zone)
    return f"{start:%H:%M}–{end:%H:%M}"


def briefing(day: Briefing) -> str:
    state = "aprobado" if day.approved else "sin aprobar"
    lines = [f"Plan de hoy {day.day.isoformat()} ({state})"]
    work = day.work
    lines += [f"• {_span(b, day.zone)} {b.title}" for b in work] or ["• Sin bloques de trabajo."]
    protected = [b for b in day.blocks if b.kind is BlockKind.PROTEGIDO]
    if protected:
        lines.append(
            "Protegido: " + ", ".join(f"{_span(b, day.zone)} {b.title}" for b in protected)
        )
    if not day.readable:
        lines.append("Aviso: la nota de hoy no sigue el formato del plan.")
    lines.append("Responde /hoy cuando quieras volver a verlo.")
    return "\n".join(lines)


def no_plan(day: date) -> str:
    return (
        f"Hoy {day.isoformat()} no hay plan aprobado. Propón y aprueba la semana "
        "en la web o con 'lifeos semana' y 'lifeos aprobar'."
    )


def closing(day: date) -> str:
    return (
        f"Hora del cierre de {day.isoformat()}: marca qué hiciste en la web o con "
        "'lifeos cierre'. Lo que no respondas no cambia."
    )


def reminder(block: Block, zone: tzinfo | None) -> str:
    return f"En unos minutos: {_span(block, zone)} {block.title}"


def proposal(week: IsoWeek, outcome: PlanOutcome) -> str:
    work = sum(1 for b in outcome.proposal.blocks if b.kind is BlockKind.TRABAJO)
    if not outcome.accepted:
        return (
            f"No pude proponer el plan de {week.render()}: el validador encontró "
            f"{len(outcome.conflicts)} conflicto(s). Revísalo con 'lifeos semana'."
        )
    lines = [f"Plan propuesto para {week.render()}: {work} bloque(s) de trabajo."]
    if outcome.proposal.unplanned:
        lines.append(f"No caben {len(outcome.proposal.unplanned)} tarea(s); se proponen aplazar.")
    lines.append("Revísalo y, si te sirve, aprueba el plan en la web o con 'lifeos aprobar'.")
    return "\n".join(lines)


def existing_proposal(view: WeekView) -> str:
    work = sum(1 for b in view.blocks if b.kind is BlockKind.TRABAJO)
    state = view.status.value if view.status else "sin estado"
    return (
        f"Plan de {view.week.render()} ({state}): {work} bloque(s) de trabajo.\n"
        "Revísalo y, si te sirve, aprueba el plan en la web o con 'lifeos aprobar'."
    )
