"""Analizador y editor sin pérdidas de líneas de tarea (spec formato-de-tareas).

El formato es regular y determinista, así que se analiza con expresiones regulares (sin LLM).
`render()` devuelve la línea original byte a byte; las ediciones reescriben solo el fragmento
afectado y vuelven a analizar el resultado para validarlo.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Final, TypedDict

from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.task import Area, Checkbox, Effort, ExplicitState, Priority, Progress, Task

# Se definen por código para que el formateador no los convierta en caracteres invisibles.
DUE = chr(0x1F4C5)  # fecha límite
SCHEDULED = chr(0x23F3)  # fecha programada
RECURRENCE = chr(0x1F501)  # recurrencia
# Fechas propias de Obsidian Tasks que se conservan pero no se interpretan:
# completada (U+2705), creada (U+2795), inicio (U+1F6EB) y cancelada (U+274C).
IGNORED_DATE_EMOJIS = (chr(0x2705), chr(0x2795), chr(0x1F6EB), chr(0x274C))
VARIATION_SELECTOR = chr(0xFE0F)

_LINE_RE = re.compile(r"(?P<indent>[ \t]*)[-*+] \[(?P<mark>.)\] (?P<body>.*)")
_DATE_EMOJIS = "".join((DUE, SCHEDULED, *IGNORED_DATE_EMOJIS))
_TOKEN_RE = re.compile(
    rf"\[(?P<key>\w+)::(?P<value>[^\]]*)\]"
    rf"|(?P<emoji>[{_DATE_EMOJIS}]){VARIATION_SELECTOR}?\s*(?P<date>\d{{4}}-\d{{2}}-\d{{2}})"
    rf"|(?P<recurrence>{RECURRENCE}){VARIATION_SELECTOR}?"
)
_DATE_FIELDS: Final[Mapping[str, str]] = MappingProxyType(
    {DUE: "fecha límite", SCHEDULED: "fecha programada"}
)
_BOOLEANS: Final[Mapping[str, bool]] = MappingProxyType({"true": True, "false": False})
KNOWN_KEYS: Final = frozenset(
    {"id", "area", "esfuerzo", "avance", "prioridad", "estado", "inferido"}
)


class _TaskFields(TypedDict, total=False):
    id: TaskId
    area: Area
    effort: Effort
    progress: Progress
    priority: Priority
    due: date
    scheduled: date
    recurrence: str | None
    state: ExplicitState
    inferred: bool


@dataclass(frozen=True, slots=True)
class _Token:
    key: str  # clave del campo, o el emoji para fechas y recurrencia
    value: str
    start: int  # posiciones relativas al cuerpo de la tarea
    end: int
    is_field: bool


@dataclass(frozen=True, slots=True)
class TaskLine:
    """Línea de tarea analizada. Inmutable; `render()` reproduce `raw` exactamente.

    Al construirse vuelve a analizar `raw`, así que `task` y `extra_fields` nunca pueden
    contradecir el texto que se escribirá en la bóveda.
    """

    raw: str
    task: Task
    extra_fields: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if _analyze(self.raw) != (self.task, self.extra_fields):
            raise ValidationError("línea", self.raw, "no coincide con la tarea indicada")

    def render(self) -> str:
        return self.raw


def _tokenize(body: str) -> tuple[_Token, ...]:
    matches = list(_TOKEN_RE.finditer(body))
    tokens: list[_Token] = []
    for position, match in enumerate(matches):
        if match.group("key") is not None:
            tokens.append(
                _Token(match["key"], match["value"].strip(), match.start(), match.end(), True)
            )
        elif match.group("emoji") is not None:
            tokens.append(_Token(match["emoji"], match["date"], match.start(), match.end(), False))
        else:
            # La recurrencia abarca el texto libre hasta el siguiente token o el final.
            end = matches[position + 1].start() if position + 1 < len(matches) else len(body)
            value = body[match.end() : end].strip()
            tokens.append(_Token(RECURRENCE, value, match.start(), end, False))
    return tuple(tokens)


def _title(body: str, tokens: tuple[_Token, ...]) -> str:
    pieces: list[str] = []
    cursor = 0
    for token in tokens:
        pieces.append(body[cursor : token.start])
        cursor = token.end
    pieces.append(body[cursor:])
    title = " ".join("".join(pieces).split())
    for emoji, field in _DATE_FIELDS.items():
        if emoji in title:  # emoji de fecha sin una fecha AAAA-MM-DD detrás
            raise ValidationError(field, title, "el emoji no va seguido de una fecha AAAA-MM-DD")
    return title


def _parse_date(field: str, text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValidationError(field, text, "fecha inexistente o mal formada") from None


def _parse_bool(text: str) -> bool:
    try:
        return _BOOLEANS[text.lower()]
    except KeyError:
        raise ValidationError("inferido", text, "se espera true o false") from None


def _apply(fields: _TaskFields, key: str, value: str) -> None:
    match key:
        case "id":
            fields["id"] = TaskId(value)
        case "area":
            fields["area"] = Area.parse(value)
        case "esfuerzo":
            fields["effort"] = Effort.parse(value)
        case "avance":
            fields["progress"] = Progress.parse(value)
        case "prioridad":
            fields["priority"] = Priority.parse(value)
        case "estado":
            fields["state"] = ExplicitState.parse(value)
        case "inferido":
            fields["inferred"] = _parse_bool(value)
        case _ if key == DUE:
            fields["due"] = _parse_date(_DATE_FIELDS[DUE], value)
        case _ if key == SCHEDULED:
            fields["scheduled"] = _parse_date(_DATE_FIELDS[SCHEDULED], value)
        case _:  # RECURRENCE
            fields["recurrence"] = value or None


def _typed_values(tokens: tuple[_Token, ...]) -> tuple[_TaskFields, tuple[tuple[str, str], ...]]:
    fields: _TaskFields = {}
    extra: list[tuple[str, str]] = []
    seen: set[str] = set()
    for token in tokens:
        if token.is_field and token.key not in KNOWN_KEYS:
            extra.append((token.key, token.value))
            continue
        if token.key in IGNORED_DATE_EMOJIS:
            continue
        if token.key in seen:
            raise ValidationError(token.key, token.value, "campo duplicado en la misma tarea")
        seen.add(token.key)
        _apply(fields, token.key, token.value)
    return fields, tuple(extra)


def _analyze(line: str) -> tuple[Task, tuple[tuple[str, str], ...]] | None:
    match = _LINE_RE.fullmatch(line)
    if match is None:
        return None
    checkbox = Checkbox.parse(match["mark"])
    body = match["body"]
    tokens = _tokenize(body)
    fields, extra = _typed_values(tokens)
    return Task(title=_title(body, tokens), checkbox=checkbox, **fields), extra


def parse_task_line(line: str) -> TaskLine | None:
    """Analiza una línea sin salto final. Devuelve None si no es una tarea."""
    analyzed = _analyze(line)
    if analyzed is None:
        return None
    task, extra = analyzed
    return TaskLine(raw=line, task=task, extra_fields=extra)


def _reparse(line: str) -> TaskLine:
    parsed = parse_task_line(line)
    if parsed is None:  # pragma: no cover - las ediciones conservan la forma de tarea
        raise ValidationError("línea", line, "la edición dejó de ser una tarea")
    return parsed


def _body_offset(line: TaskLine) -> tuple[int, str]:
    match = _LINE_RE.fullmatch(line.raw)
    if match is None:  # pragma: no cover - TaskLine solo se construye desde líneas válidas
        raise ValidationError("línea", line.raw, "no es una tarea")
    return match.start("body"), match["body"]


def with_id(line: TaskLine, task_id: TaskId) -> TaskLine:
    """Inserta `[id:: …]` justo después del título sin tocar el resto de la línea."""
    if line.task.id is not None:
        raise ValidationError("id", line.task.id.value, "la tarea ya tiene id")
    offset, body = _body_offset(line)
    tokens = _tokenize(body)
    title_end = len(body[: tokens[0].start].rstrip()) if tokens else len(body.rstrip())
    position = offset + title_end
    return _reparse(f"{line.raw[:position]} [id:: {task_id.value}]{line.raw[position:]}")


def with_checkbox(line: TaskLine, checkbox: Checkbox) -> TaskLine:
    """Cambia solo el carácter de la casilla."""
    match = _LINE_RE.fullmatch(line.raw)
    if match is None:  # pragma: no cover - TaskLine solo se construye desde líneas válidas
        raise ValidationError("línea", line.raw, "no es una tarea")
    position = match.start("mark")
    return _reparse(f"{line.raw[:position]}{checkbox.value}{line.raw[position + 1 :]}")


def with_field(line: TaskLine, key: str, value: str) -> TaskLine:
    """Reemplaza el valor de un campo existente o lo añade al final de la línea."""
    if not re.fullmatch(r"\w+", key) or "]" in value or "\n" in value:
        raise ValidationError(key, value, "clave o valor no válidos para un campo inline")
    offset, body = _body_offset(line)
    field = f"[{key}:: {value}]"
    for token in _tokenize(body):
        if token.is_field and token.key == key:
            start, end = offset + token.start, offset + token.end
            return _reparse(f"{line.raw[:start]}{field}{line.raw[end:]}")
    content_end = len(line.raw.rstrip())
    return _reparse(f"{line.raw[:content_end]} {field}{line.raw[content_end:]}")
