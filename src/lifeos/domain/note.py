"""Operaciones puras sobre el texto completo de una nota, respetando BOM y saltos de línea."""

import re
from dataclasses import dataclass

from lifeos.domain.errors import ValidationError
from lifeos.domain.task_line import TaskLine, parse_task_line

BOM = chr(0xFEFF)  # marca de orden de bytes UTF-8
CRLF = "\r\n"
LF = "\n"
_LINE_SPLIT_RE = re.compile(r"(?<=\n)")


@dataclass(frozen=True, slots=True)
class NoteTask:
    index: int  # número de línea, empezando en 0
    line: TaskLine


@dataclass(frozen=True, slots=True)
class ParseProblem:
    index: int
    content: str
    error: ValidationError


@dataclass(frozen=True, slots=True)
class ParsedNote:
    tasks: tuple[NoteTask, ...]
    problems: tuple[ParseProblem, ...]


def _split(text: str) -> list[tuple[str, str]]:
    """Separa en pares (contenido, salto) por `\\n` o `\\r\\n` únicamente.

    `str.splitlines` también corta en separadores Unicode (U+0085, U+2028, \\f…) que Obsidian
    no considera saltos de línea; usarlo desalinearía los índices respecto al editor.
    """
    pairs: list[tuple[str, str]] = []
    for raw in _LINE_SPLIT_RE.split(text):
        if not raw:
            continue
        ending = CRLF if raw.endswith(CRLF) else LF if raw.endswith(LF) else ""
        pairs.append((raw[: len(raw) - len(ending)], ending))
    return pairs


def split_lines(text: str) -> tuple[str, ...]:
    """Contenido de cada línea, con la misma división que usan las demás operaciones."""
    return tuple(content for content, _ending in _split(text))


def _join(pairs: list[tuple[str, str]]) -> str:
    return "".join(content + ending for content, ending in pairs)


def _check_single_line(content: str) -> None:
    if "\n" in content or "\r" in content:
        raise ValidationError("línea", content, "no puede contener saltos de línea")


def _check_index(pairs: list[tuple[str, str]], index: int) -> None:
    if not 0 <= index < len(pairs):
        raise ValidationError("línea", index, f"fuera de rango (la nota tiene {len(pairs)})")


def parse_note(text: str) -> ParsedNote:
    """Localiza las tareas de una nota. Los errores se acumulan sin detener el análisis."""
    tasks: list[NoteTask] = []
    problems: list[ParseProblem] = []
    for index, (content, _ending) in enumerate(_split(text)):
        candidate = content.removeprefix(BOM) if index == 0 else content
        try:
            parsed = parse_task_line(candidate)
        except ValidationError as error:
            problems.append(ParseProblem(index, candidate, error))
            continue
        if parsed is not None:
            tasks.append(NoteTask(index, parsed))
    return ParsedNote(tuple(tasks), tuple(problems))


def replace_line(text: str, index: int, content: str) -> str:
    _check_single_line(content)
    pairs = _split(text)
    _check_index(pairs, index)
    old_content, ending = pairs[index]
    prefix = BOM if index == 0 and old_content.startswith(BOM) else ""
    pairs[index] = (prefix + content, ending)
    return _join(pairs)


def remove_line(text: str, index: int) -> str:
    pairs = _split(text)
    _check_index(pairs, index)
    removed, _ending = pairs.pop(index)
    if index == 0 and removed.startswith(BOM) and pairs:
        first, ending = pairs[0]
        pairs[0] = (BOM + first, ending)
    return _join(pairs)


def append_line(text: str, content: str) -> str:
    _check_single_line(content)
    newline = CRLF if CRLF in text else LF
    separator = newline if text and not text.endswith(LF) else ""
    return f"{text}{separator}{content}{newline}"
