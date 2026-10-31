"""Las listas fijas del frontend (`web/src`) coinciden con los enums del dominio.

El frontend solo ofrece opciones; si el dominio añade o cambia un valor, esta prueba falla en
lugar de dejar la web desfasada sin aviso (revisión de la Fase 5).
"""

import re
from enum import StrEnum
from pathlib import Path

import pytest

from lifeos.domain.closing import AnswerKind
from lifeos.domain.coach import CheckInState
from lifeos.domain.task import Area, TaskStatus

WEB = Path(__file__).resolve().parent.parent.parent / "web" / "src"

pytestmark = pytest.mark.unit


def _strings(source: str, name: str) -> set[str]:
    """Cadenas de `const NAME = [...]` en un archivo del frontend."""
    match = re.search(rf"const {name} = \[(.*?)\];", source, re.S)
    assert match is not None, name
    return set(re.findall(r'"([a-z_ ]*)"', match[1]))


def _values(kind: type[StrEnum]) -> set[str]:
    return {item.value for item in kind}


def test_checkin_states_match_the_domain() -> None:
    source = (WEB / "views" / "CoachChat.tsx").read_text(encoding="utf-8")
    states = set(re.findall(r'\["([a-z]+)", "', source))
    assert states == _values(CheckInState)


def test_task_filters_match_the_domain() -> None:
    source = (WEB / "views" / "TasksView.tsx").read_text(encoding="utf-8")
    assert _strings(source, "AREAS") - {""} == _values(Area)
    assert _strings(source, "STATUSES") - {""} == _values(TaskStatus)


def test_closing_answer_kinds_match_the_domain() -> None:
    source = (WEB / "api" / "types.ts").read_text(encoding="utf-8")
    match = re.search(r'tipo: ("[a-z]+"(?: \| "[a-z]+")*);', source)
    assert match is not None
    assert set(re.findall(r'"([a-z]+)"', match[1])) == _values(AnswerKind)
