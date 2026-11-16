"""Regresiones de la Fase 9.

`closing_score` (carga adaptativa, DT-01) solo aceptaba `- HH:MM · texto`, pero el cierre real
escribe `- HH:MM — texto` (`CheckInEntry.render`). La carga adaptativa y la racha de cierres no
veían ningún cierre real. Se encontró con la E2E de la Oficina (F9-T04).
"""

from datetime import time

import pytest

from lifeos.domain.adaptive_load import DayScore, closing_score
from lifeos.domain.closing import AnswerKind, TaskChange, summary_entries
from lifeos.domain.ids import TaskId

pytestmark = pytest.mark.unit


def _note_with_real_closing() -> str:
    changes = [
        TaskChange(TaskId("t-aaaaaa"), "Informe", AnswerKind.HECHA, None, None, None, None),
        TaskChange(TaskId("t-bbbbbb"), "Ensayo", AnswerKind.NO, None, None, None, None),
    ]
    entries = summary_entries(changes, [TaskId("t-cccccc")], time(22, 30))
    lines = "\n".join(entry.render() for entry in entries)
    return f"# Día\n\n## Cierre\n{lines}\n"


def test_closing_score_reads_the_lines_the_real_closing_writes() -> None:
    assert closing_score(_note_with_real_closing()) == DayScore(1.0, 2)


def test_closing_score_still_reads_lines_without_time() -> None:
    assert closing_score("## Cierre\n- hecha: t-aaaaaa\n") == DayScore(1.0, 1)
