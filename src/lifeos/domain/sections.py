"""Secciones de nota (`## Título`) y comprobación de que un cambio solo inserta en una de ellas.

Una sección empieza en su título de nivel 2 y termina en el siguiente título de nivel 1 o 2
(los subtítulos `###` quedan dentro). Los títulos dentro de bloques de código no cuentan.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from lifeos.domain.errors import ValidationError
from lifeos.domain.note import BOM, split_lines

_SECTION_HEADING_RE = re.compile(r"^##[ \t]+(.+?)[ \t#]*$")
_BOUNDARY_RE = re.compile(r"^#{1,2}[ \t]")
_FENCE_PREFIXES = ("```", "~~~")
# Líneas que cambiarían la estructura o los datos de la nota si se anexaran: tareas, campos en
# línea y reglas `---`/`===` (que convierten la línea anterior en título).
_TASK_RE = re.compile(r"^\s*[-*+]\s+\[[^\]]?\]")
_RULE_RE = re.compile(r"^\s*(=+|-{2,}|(\*\s*){3,}|(_\s*){3,})\s*$")


@dataclass(frozen=True, slots=True)
class SectionSpan:
    heading_index: int
    end: int  # primera línea que ya no pertenece a la sección


def _lines(text: str) -> list[str]:
    lines = list(split_lines(text))
    if lines:
        lines[0] = lines[0].removeprefix(BOM)
    return lines


def _is_fence(line: str) -> bool:
    return line.lstrip().startswith(_FENCE_PREFIXES)


def _structural_lines(lines: Sequence[str]) -> list[tuple[int, str]]:
    """Líneas fuera de bloques de código, con su índice."""
    visible: list[tuple[int, str]] = []
    in_fence = False
    for index, line in enumerate(lines):
        if _is_fence(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            visible.append((index, line))
    return visible


def _ends_inside_fence(lines: Sequence[str]) -> bool:
    return sum(1 for line in lines if _is_fence(line)) % 2 == 1


def _find(lines: Sequence[str], heading: str) -> SectionSpan | None:
    wanted = heading.strip().casefold()
    visible = _structural_lines(lines)
    starts = [
        index
        for index, line in visible
        if (match := _SECTION_HEADING_RE.match(line)) and match[1].strip().casefold() == wanted
    ]
    if not starts:
        return None
    if len(starts) > 1:
        raise ValidationError("sección", heading, "duplicada en la nota; no se sabe dónde anexar")
    start = starts[0]
    end = next(
        (index for index, line in visible if index > start and _BOUNDARY_RE.match(line)),
        len(lines),
    )
    return SectionSpan(start, end)


def find_section(text: str, heading: str) -> SectionSpan | None:
    return _find(_lines(text), heading)


def section_lines(text: str, heading: str) -> tuple[str, ...] | None:
    """Líneas de la sección (título incluido) sin las líneas en blanco finales."""
    lines = _lines(text)
    span = _find(lines, heading)
    if span is None:
        return None
    body = lines[span.heading_index : span.end]
    while body and not body[-1].strip():
        body.pop()
    return tuple(body)


def section_insert_index(text: str, span: SectionSpan) -> int:
    """Posición tras la última línea no vacía de la sección (o justo bajo su título)."""
    lines = _lines(text)
    last = span.heading_index
    for index in range(span.heading_index + 1, span.end):
        if lines[index].strip():
            last = index
    return last + 1


def _is_structural(line: str) -> bool:
    """Línea que no puede anexarse: título, valla, regla, tarea o campo en línea."""
    return (
        line.lstrip().startswith("#")
        or _is_fence(line)
        or bool(_RULE_RE.match(line))
        or bool(_TASK_RE.match(line))
        or "::" in line
    )


def _is_new_section_block(block: Sequence[str], heading: str) -> bool:
    rest = list(block)
    while rest and not rest[0].strip():
        rest.pop(0)
    if not rest:
        return False
    match = _SECTION_HEADING_RE.match(rest[0])
    if match is None or match[1].strip().casefold() != heading.strip().casefold():
        return False
    return not any(_is_structural(line) for line in rest[1:])


def only_inserted_in_section(old: str, new: str, heading: str) -> bool:
    """`new` es `old` con un bloque de líneas insertado dentro de la sección `heading`.

    Si la sección no existe, solo se admite añadirla al final con su título. El bloque no puede
    contener títulos ni delimitadores de código, para no alterar la estructura de la nota.
    """
    old_lines, new_lines = _lines(old), _lines(new)
    added = len(new_lines) - len(old_lines)
    if added <= 0:
        return False
    span = _find(old_lines, heading)
    for position in range(len(old_lines) + 1):
        if (
            new_lines[:position] != old_lines[:position]
            or new_lines[position + added :] != old_lines[position:]
        ):
            continue
        block = new_lines[position : position + added]
        if span is None:
            appendable = position == len(old_lines) and not _ends_inside_fence(old_lines)
            if appendable and _is_new_section_block(block, heading):
                return True
        elif span.heading_index < position <= span.end and not any(map(_is_structural, block)):
            return True
    return False
