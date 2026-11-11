"""Metas por área (DT-02, D-14, spec metas-por-area).

Formato en `<carpeta del área>/metas.md`, una meta por línea (no son tareas: sin casilla):

    - [meta:: m1] Aprobar Redes con 16 o más [horizonte:: 2026-12-15] [estado:: activa]

Los estados son los de vigencia de PLAN §6. Sin `estado`, la meta está activa. Una línea mal
formada se cuenta como problema y no se adivina. Los compromisos fijos ya viven en
`_sistema/horario.md` (D-14); las metas no generan bloques: solo informan a los especialistas.
"""

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from lifeos.domain.plan import clean
from lifeos.domain.task import Area

MAX_GOALS = 50
MAX_GOAL_TITLE = 160
_GOAL_LINE_RE = re.compile(r"\s*[-*+] \[meta::")
_FIELD_RE = re.compile(r"\[([a-z_]+)::\s*([^\[\]]*?)\s*\]")
_KEY_RE = re.compile(r"m[0-9]{1,4}")
_ALLOWED_FIELDS = frozenset({"meta", "horizonte", "estado"})


class GoalStatus(StrEnum):
    ACTIVA = "activa"
    PENDIENTE_CONFIRMACION = "pendiente_confirmacion"
    SUSTITUIDA = "sustituida"
    ARCHIVADA = "archivada"
    COMPLETADA = "completada"


@dataclass(frozen=True, slots=True)
class Goal:
    key: str
    title: str
    area: Area
    horizon: date | None
    status: GoalStatus


@dataclass(frozen=True, slots=True)
class GoalsReport:
    goals: tuple[Goal, ...]
    problems: int

    @property
    def open_goals(self) -> tuple[Goal, ...]:
        return tuple(goal for goal in self.goals if goal.status is GoalStatus.ACTIVA)


def _parse_line(line: str, area: Area) -> Goal | None:
    fields: dict[str, str] = {}
    for key, value in _FIELD_RE.findall(line):
        if key not in _ALLOWED_FIELDS or key in fields:
            return None
        fields[key] = value
    title = clean(_FIELD_RE.sub(" ", line.strip()[2:]), MAX_GOAL_TITLE)
    key = fields.get("meta", "")
    if not _KEY_RE.fullmatch(key) or not title:
        return None
    try:
        horizon = date.fromisoformat(fields["horizonte"]) if "horizonte" in fields else None
        status = GoalStatus(fields.get("estado", GoalStatus.ACTIVA))
    except ValueError:
        return None
    return Goal(key, title, area, horizon, status)


def parse_goals(text: str, area: Area) -> GoalsReport:
    goals: list[Goal] = []
    seen: set[str] = set()
    problems = 0
    for line in text.splitlines():
        if not _GOAL_LINE_RE.match(line):
            continue
        goal = _parse_line(line, area)
        if goal is None or goal.key in seen or len(goals) >= MAX_GOALS:
            problems += 1
            continue
        seen.add(goal.key)
        goals.append(goal)
    return GoalsReport(tuple(goals), problems)
