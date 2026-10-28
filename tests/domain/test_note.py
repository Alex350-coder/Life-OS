"""Operaciones puras sobre el texto de una nota: localizar, reemplazar, quitar y anexar líneas."""

import pytest

from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.note import (
    BOM,
    append_line,
    insert_line,
    parse_note,
    remove_line,
    replace_line,
)

pytestmark = pytest.mark.unit

NOTE = (
    "# Semana\n"
    "- [ ] Leer capítulo [id:: t-222222] [area:: universidad] [esfuerzo:: 1h]\n"
    "Texto libre\n"
    "- [x] Pagar luz [area:: cotidiano] [esfuerzo:: 15m]\n"
)


def test_parse_note_locates_tasks_with_line_index() -> None:
    parsed = parse_note(NOTE)

    assert [t.index for t in parsed.tasks] == [1, 3]
    assert parsed.tasks[0].line.task.id == TaskId("t-222222")
    assert parsed.problems == ()


def test_parse_note_collects_problems_without_stopping() -> None:
    text = "- [ ] Mala [esfuerzo:: 0h]\n- [ ] Buena [area:: salud] [esfuerzo:: 30m]\n"

    parsed = parse_note(text)

    assert [t.index for t in parsed.tasks] == [1]
    assert len(parsed.problems) == 1
    assert parsed.problems[0].index == 0
    assert isinstance(parsed.problems[0].error, ValidationError)


def test_parse_note_handles_crlf_and_bom_on_first_line() -> None:
    text = "﻿- [ ] Primera [area:: salud] [esfuerzo:: 30m]\r\notra\r\n"

    parsed = parse_note(text)

    assert parsed.tasks[0].line.task.title == "Primera"


def test_replace_line_keeps_original_line_ending() -> None:
    text = "a\r\nb\r\nc"

    assert replace_line(text, 1, "B") == "a\r\nB\r\nc"
    assert replace_line(text, 2, "C") == "a\r\nb\r\nC"


def test_replace_line_keeps_bom_on_first_line() -> None:
    assert replace_line("﻿a\nb\n", 0, "A") == "﻿A\nb\n"


def test_replace_line_out_of_range_raises() -> None:
    with pytest.raises(ValidationError, match="línea"):
        replace_line("a\n", 5, "x")


def test_remove_line_removes_only_that_line() -> None:
    assert remove_line("a\nb\nc\n", 1) == "a\nc\n"
    assert remove_line("﻿a\nb\n", 0) == "﻿b\n"


def test_remove_line_out_of_range_raises() -> None:
    with pytest.raises(ValidationError, match="línea"):
        remove_line("a\n", 1)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "x\n"),
        ("a\n", "a\nx\n"),
        ("a", "a\nx\n"),
        ("a\r\nb\r\n", "a\r\nb\r\nx\r\n"),
        ("a\r\nb", "a\r\nb\r\nx\r\n"),
    ],
)
def test_append_line_uses_note_newline_style(text: str, expected: str) -> None:
    assert append_line(text, "x") == expected


def test_line_operations_reject_embedded_newlines() -> None:
    with pytest.raises(ValidationError, match="línea"):
        append_line("a\n", "x\ny")
    with pytest.raises(ValidationError, match="línea"):
        replace_line("a\n", 0, "x\ry")


# --- insert_line (Fase 2): anexar dentro de una sección sin tocar el resto ---------------


@pytest.mark.parametrize(
    ("text", "index", "expected"),
    [
        ("a\nc\n", 1, "a\nb\nc\n"),
        ("a\r\nc\r\n", 1, "a\r\nb\r\nc\r\n"),
        ("a\nc\n", 2, "a\nc\nb\n"),
        ("a\nc", 2, "a\nc\nb\n"),
        ("", 0, "b\n"),
        (BOM + "a\n", 0, BOM + "b\na\n"),
    ],
)
def test_insert_line_keeps_newlines_and_bom(text: str, index: int, expected: str) -> None:
    assert insert_line(text, index, "b") == expected


def test_insert_line_rejects_bad_index_and_embedded_newlines() -> None:
    with pytest.raises(ValidationError):
        insert_line("a\n", 5, "b")
    with pytest.raises(ValidationError):
        insert_line("a\n", 0, "b\nc")
