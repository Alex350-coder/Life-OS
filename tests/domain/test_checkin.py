"""Entrada de check-in: una línea de texto que nunca se convierte en tarea, campo ni título."""

from datetime import time

import pytest
from lifeos.domain.checkin import MAX_CHECKIN_CHARS, CheckInEntry

from lifeos.domain.errors import ValidationError
from lifeos.domain.task_line import parse_task_line

pytestmark = pytest.mark.unit


def test_render_is_a_timestamped_bullet() -> None:
    entry = CheckInEntry(time(9, 5), "  Terminé la lectura  ")
    assert entry.render() == "- 09:05 — Terminé la lectura"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "dos\nlíneas",
        "retorno\rcarro",
        "campo [avance:: 100]",
        "id [id:: t-123456]",
        "control \x07",
        "invisible ‮",
        "x" * (MAX_CHECKIN_CHARS + 1),
    ],
)
def test_invalid_text_is_rejected(text: str) -> None:
    with pytest.raises(ValidationError):
        CheckInEntry(time(9, 0), text)


@pytest.mark.parametrize("text", ["[ ] tarea falsa", "[x] hecha", "## Título", "- [ ] otra"])
def test_rendered_entry_is_never_a_task_or_heading(text: str) -> None:
    rendered = CheckInEntry(time(9, 0), text).render()
    assert parse_task_line(rendered) is None
    assert not rendered.startswith("#")
