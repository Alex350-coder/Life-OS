"""Operaciones puras sobre el texto completo de una nota, respetando BOM y saltos de línea."""

from dataclasses import dataclass

from lifeos.domain.errors import ValidationError
from lifeos.domain.task_line import TaskLine, parse_task_line

BOM = chr(0xFEFF)  # marca de orden de bytes UTF-8
CRLF = "\r\n"
LF = "\n"


@dataclass(frozen=True, slots=True)
class NoteTask:
    index: int  # número de línea, empezando en 0
    line: TaskLine


@dataclass(frozen=True, slots=True)
class ParseProblem:
    index: int
    error: ValidationError


@dataclass(frozen=True, slots=True)
class ParsedNote:
    tasks: tuple[NoteTask, ...]
    problems: tuple[ParseProblem, ...]


def _split(text: str) -> list[tuple[str, str]]:
    """Separa en pares (contenido, salto) sin perder el tipo de salto de cada línea."""
    pairs = []
    for raw in text.splitlines(keepends=True):
        ending = CRLF if raw.endswith(CRLF) else LF if raw.endswith(LF) else ""
        pairs.append((raw[: len(raw) - len(ending)], ending))
    return pairs


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
            problems.append(ParseProblem(index, error))
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
    if text and not text.endswith((LF, "\r")):
        text += newline
    return f"{text}{content}{newline}"
