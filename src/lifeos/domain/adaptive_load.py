"""Carga adaptativa del Jefe (DT-01, `docs/specs/carga-adaptativa.md`).

Regla determinista y explicable: con los cierres de los últimos días, el Jefe baja el foco diario
de la semana siguiente si el propietario está cumpliendo poco. Nunca lo sube por encima de
`foco_max_dia`: ese límite lo pone el propietario y es un techo, no un objetivo.

Puntuación de cada tarea respondida en `## Cierre`: hecha = 1, avance = 0,5, no realizada = 0.
Las tareas "sin respuesta" no cuentan.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from lifeos.domain.sections import section_lines

CLOSING_SECTION = "cierre"
WINDOW_DAYS = 7  # cierres de la última semana
MIN_CLOSED_DAYS = 3  # con menos días no hay señal suficiente: se respeta el límite tal cual
HIGH_RATIO = 0.8
LOW_RATIO = 0.5
MEDIUM_PERCENT = 85
LOW_PERCENT = 70
ROUND_MINUTES = 30
FLOOR_MINUTES = 60
FULL_PERCENT = 100
_SCORES = (("hecha:", 1.0), ("avance:", 0.5), ("no realizada:", 0.0))
_ENTRY_RE = re.compile(r"^\s*-\s*(?:\d{1,2}:\d{2}\s*·\s*)?(?P<body>.+)$")


@dataclass(frozen=True, slots=True)
class DayScore:
    points: float
    answered: int


@dataclass(frozen=True, slots=True)
class LoadAdvice:
    focus_max: int  # minutos de foco por día para la propuesta
    ratio: float | None  # cumplimiento medio, o None sin datos suficientes
    closed_days: int
    reason: str


def closing_score(note_text: str) -> DayScore | None:
    """Puntuación del cierre de un día, o None si no se cerró."""
    lines = section_lines(note_text, CLOSING_SECTION)
    if not lines:
        return None
    points, answered = 0.0, 0
    for line in lines[1:]:
        match = _ENTRY_RE.match(line)
        if match is None:
            continue
        body = match["body"].strip()
        for prefix, value in _SCORES:
            if body.startswith(prefix):
                points += value
                answered += 1
                break
    return DayScore(points, answered) if answered else None


def _rounded(minutes: int) -> int:
    return max(FLOOR_MINUTES, minutes // ROUND_MINUTES * ROUND_MINUTES)


def advise(focus_max: int, scores: Iterable[DayScore]) -> LoadAdvice:
    days = list(scores)
    if len(days) < MIN_CLOSED_DAYS:
        return LoadAdvice(
            focus_max,
            None,
            len(days),
            f"carga sin ajustar: {len(days)} cierres recientes (hacen falta {MIN_CLOSED_DAYS})",
        )
    # Redondeo: 2,4 de 3 es 0,8 exacto, no 0,7999… por el error de coma flotante.
    ratio = round(sum(d.points for d in days) / sum(d.answered for d in days), 6)
    if ratio >= HIGH_RATIO:
        percent = FULL_PERCENT
    elif ratio >= LOW_RATIO:
        percent = MEDIUM_PERCENT
    else:
        percent = LOW_PERCENT
    focus = (
        focus_max
        if percent == FULL_PERCENT
        else min(focus_max, _rounded(focus_max * percent // FULL_PERCENT))
    )
    hours = f"{focus // 60}h{focus % 60:02d}" if focus % 60 else f"{focus // 60}h"
    if focus == focus_max:
        reason = f"carga completa ({hours}/día): cumpliste el {ratio:.0%} en {len(days)} cierres"
    else:
        reason = (
            f"carga reducida a {hours}/día: cumpliste el {ratio:.0%} en {len(days)} cierres; "
            "sube sola cuando vuelvas a cumplir"
        )
    return LoadAdvice(focus, ratio, len(days), reason)
