"""Secciones de nota (`## Título`) y verificación de que un cambio solo inserta líneas en una."""

import pytest

from lifeos.domain.errors import ValidationError
from lifeos.domain.sections import (
    find_section,
    only_inserted_in_section,
    section_insert_index,
)

pytestmark = pytest.mark.unit

DAILY = (
    "# Martes\n"  # 0
    "## Plan\n"  # 1
    "- [ ] Leer [id:: t-222222] [area:: universidad] [esfuerzo:: 1h]\n"  # 2
    "\n"  # 3
    "## Check-ins\n"  # 4
    "- 08:00 — Empecé\n"  # 5
    "### Detalle\n"  # 6 (subtítulo: sigue dentro de la sección)
    "texto\n"  # 7
    "\n"  # 8
    "## Notas\n"  # 9
    "libre\n"  # 10
)


def test_find_section_is_case_insensitive_and_spans_until_next_level_two_heading() -> None:
    span = find_section(DAILY, "check-ins")
    assert span is not None
    assert (span.heading_index, span.end) == (4, 9)


def test_find_section_returns_none_when_absent_and_ignores_level_three() -> None:
    assert find_section(DAILY, "Detalle") is None
    assert find_section(DAILY, "Inexistente") is None


def test_headings_inside_code_fences_are_ignored() -> None:
    text = "```\n## Check-ins\n```\n## Check-ins\n- 08:00 — x\n"
    span = find_section(text, "Check-ins")
    assert span is not None
    assert span.heading_index == 3


def test_duplicate_section_is_ambiguous() -> None:
    with pytest.raises(ValidationError, match="duplicada"):
        find_section("## Check-ins\n## check-ins\n", "Check-ins")


def test_insert_index_is_after_last_non_blank_line_of_section() -> None:
    span = find_section(DAILY, "Check-ins")
    assert span is not None
    assert section_insert_index(DAILY, span) == 8
    empty = "## Check-ins\n\n## Notas\n"
    empty_span = find_section(empty, "Check-ins")
    assert empty_span is not None
    assert section_insert_index(empty, empty_span) == 1


# --- only_inserted_in_section -------------------------------------------------------------


def _with(lines: list[str], index: int, *new: str) -> str:
    return "".join(f"{line}\n" for line in [*lines[:index], *new, *lines[index:]])


LINES = DAILY.splitlines()


@pytest.mark.parametrize("index", [5, 6, 8, 9])
def test_insertion_inside_section_is_accepted(index: int) -> None:
    assert only_inserted_in_section(DAILY, _with(LINES, index, "- 09:00 — nueva"), "check-ins")


@pytest.mark.parametrize("index", [0, 3, 4, 10, 11])
def test_insertion_outside_section_is_rejected(index: int) -> None:
    assert not only_inserted_in_section(DAILY, _with(LINES, index, "- 09:00 — x"), "check-ins")


def test_editing_or_removing_existing_lines_is_rejected() -> None:
    edited = DAILY.replace("- [ ] Leer", "- [x] Leer")
    assert not only_inserted_in_section(DAILY, edited, "check-ins")
    removed = DAILY.replace("- 08:00 — Empecé\n", "")
    assert not only_inserted_in_section(DAILY, removed, "check-ins")
    assert not only_inserted_in_section(DAILY, DAILY, "check-ins")  # sin cambios


@pytest.mark.parametrize("line", ["## Plan", "# Otro", "```", "~~~"])
def test_inserted_lines_cannot_add_headings_or_fences(line: str) -> None:
    assert not only_inserted_in_section(DAILY, _with(LINES, 8, line), "check-ins")


def test_new_section_may_be_appended_at_end_when_missing() -> None:
    old = "# Martes\n## Plan\n- algo\n"
    assert only_inserted_in_section(old, old + "\n## Check-ins\n- 09:00 — x\n", "check-ins")
    assert only_inserted_in_section(old, old + "## Check-ins\n- 09:00 — x\n", "check-ins")
    assert not only_inserted_in_section(old, old + "## Plan 2\n- x\n", "check-ins")
    assert not only_inserted_in_section(old, "## Check-ins\n- x\n" + old, "check-ins")
    assert not only_inserted_in_section(old, old + "## Check-ins\n- x\n## Plan\n- y\n", "check-ins")
