"""Constructores de notas diarias y check-ins para las pruebas de la Fase 4 (datos inventados).

Las notas siguen el formato que publica el Jefe (`domain/plan_note.render_daily`), escrito a
mano para que las pruebas no dependan del código que verifican.
"""

from collections.abc import Sequence
from datetime import date

from tests.planning_builders import MON, TUE, WEEK

TASKS = (
    "- [ ] Informe de redes [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 3h] "
    "[avance:: 25%] 📅 2026-11-04\n"
    "- [ ] Leer capítulo [id:: t-bbbbbb] [area:: universidad] [esfuerzo:: 1h]\n"
    "- [ ] Práctica de SQL [id:: t-cccccc] [area:: universidad] [esfuerzo:: 2h]\n"
)


def work_line(span: str, title: str, task_id: str) -> str:
    return f"- {span} · {title} [id:: {task_id}]"


def fixed_line(span: str, title: str) -> str:
    return f"- {span} · {title} [tipo:: fijo]"


def protected_line(span: str, title: str) -> str:
    return f"- {span} · {title} [tipo:: protegido]"


def daily_text(
    day: date,
    *,
    work: Sequence[str] = (),
    fixed: Sequence[str] = (),
    protected: Sequence[str] = (),
    checkins: Sequence[str] = (),
    closing: Sequence[str] = (),
    week: str = WEEK.render(),
    status: str = "aprobado",
) -> str:
    sections = (
        ("Lo no negociable", fixed),
        ("Bloques", work),
        ("Tiempo protegido", protected),
        ("Check-ins", checkins),
        ("Cierre", closing),
    )
    lines = [
        "---",
        "tipo: diario",
        f"fecha: {day.isoformat()}",
        f"estado_plan: {status}",
        "generado_por: jefe",
        f"semana: {week}",
        "---",
        "",
        f"# Plan del día {day.isoformat()}",
        "",
    ]
    for heading, items in sections:
        lines += [f"## {heading}", *items, ""]
    return "\n".join(lines)


def monday_note(*, checkins: Sequence[str] = (), closing: Sequence[str] = ()) -> str:
    """Lunes 2026-11-02 con el horario de prueba: clase fija 10–12 y tres bloques de trabajo."""
    return daily_text(
        MON,
        fixed=(fixed_line("10:00–12:00", "Clase de redes"),),
        work=(
            work_line("08:00–09:30", "Informe de redes", "t-aaaaaa"),
            work_line("13:00–14:00", "Leer capítulo", "t-bbbbbb"),
            work_line("15:00–17:00", "Práctica de SQL", "t-cccccc"),
        ),
        checkins=checkins,
        closing=closing,
    )


def tuesday_note() -> str:
    """Martes 2026-11-03 con el gimnasio protegido de 18:00 a 19:30."""
    return daily_text(
        TUE,
        work=(work_line("08:00–10:00", "Informe de redes", "t-aaaaaa"),),
        protected=(protected_line("18:00–19:30", "Gym"),),
    )
