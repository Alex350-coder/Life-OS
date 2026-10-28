"""Analizador determinista de `_sistema/horario.md` (spec horario-y-limites; D-13, D-14).

El formato es regular, así que se analiza con expresiones regulares y sin LLM. Se informan todos
los problemas a la vez. El texto de la nota es un dato: un título nunca cambia límites ni reglas.
"""

import re
from collections.abc import Iterator, Mapping
from datetime import date
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lifeos.domain.errors import DomainError, ValidationError
from lifeos.domain.schedule import (
    DEFAULT_BLOCK_MAX,
    DEFAULT_BLOCK_MIN,
    Limits,
    RuleKind,
    Schedule,
    ScheduleRule,
    TimeRange,
    Vigencia,
    parse_days,
)

HORARIO_NOTE = "_sistema/horario.md"
REQUIRED_LIMITS = ("foco_max_dia", "libre_min_dia", "margen")
OPTIONAL_LIMITS: Mapping[str, int] = MappingProxyType(
    {"bloque_minimo": DEFAULT_BLOCK_MIN, "bloque_maximo": DEFAULT_BLOCK_MAX}
)
_SECTION_LEVEL = 2  # las secciones del horario son encabezados "## "
_LIMITS_SECTION = "límites"
_RULE_SECTIONS: Mapping[str, RuleKind] = MappingProxyType(
    {
        "disponibilidad": RuleKind.DISPONIBLE,
        "compromisos fijos": RuleKind.FIJO,
        "tiempo protegido": RuleKind.PROTEGIDO,
        "no disponible": RuleKind.NO_DISPONIBLE,
    }
)
_HEADING_RE = re.compile(r"(#{1,6})\s+(.*?)\s*#*\s*")
_ITEM_RE = re.compile(r"\s*[-*+]\s+(.*)")
_FIELD_RE = re.compile(r"\[(\w+)::([^\]]*)\]")
_RULE_RE = re.compile(
    r"(?P<days>\S+)\s+(?P<range>todo el día|\d{2}:\d{2}\s*[–-]\s*\d{2}:\d{2})"
    r"(?:\s*·\s*(?P<title>.*?))?",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)([mh])")
_PERCENT_RE = re.compile(r"(\d+)\s*%")
_ZONE_RE = re.compile(r"^zona:\s*(\S+)\s*$", re.MULTILINE)


class HorarioError(DomainError):
    """`horario.md` no se puede usar; `problems` enumera todos los motivos."""

    def __init__(self, problems: tuple[str, ...], *, prefix: str = "horario.md no válido") -> None:
        super().__init__(f"{prefix}: " + "; ".join(problems))
        self.problems = problems


class HorarioIncompletoError(HorarioError):
    """El propietario aún no completó sus límites reales (D-13). No se planifica sin ellos."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        super().__init__(
            tuple(f"falta {name}" for name in missing),
            prefix=f"horario incompleto (D-13): complétalo en {HORARIO_NOTE}",
        )
        self.missing = missing


def _normalize(heading: str) -> str:
    return heading.casefold().replace("limites", "límites")


def _lines(text: str) -> list[str]:
    return text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _frontmatter(lines: list[str]) -> tuple[str, int]:
    if not lines or lines[0].strip() != "---":
        return "", 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index]), index + 1
    return "", 0


def _items(lines: list[str], start: int) -> Iterator[tuple[int, str, str]]:
    """Recorre los elementos de lista con la sección (normalizada) en la que aparecen."""
    section: str | None = None
    in_comment = False
    for number, line in enumerate(lines[start:], start=start + 1):
        if in_comment or line.lstrip().startswith("<!--"):
            in_comment = "-->" not in line
            continue
        heading = _HEADING_RE.fullmatch(line)
        if heading:
            section = _normalize(heading[2]) if len(heading[1]) == _SECTION_LEVEL else None
            continue
        item = _ITEM_RE.fullmatch(line)
        if item and section is not None:
            yield number, section, item[1].strip()


def _duration(name: str, text: str) -> int:
    match = _DURATION_RE.fullmatch(text)
    if match is None:
        raise ValidationError(name, text, "formato esperado: 30m, 1.5h o 4h")
    amount = float(match[1]) * (60 if match[2] == "h" else 1)
    if amount != int(amount):
        raise ValidationError(name, text, "debe equivaler a minutos enteros")
    return int(amount)


def _percent(text: str) -> int:
    match = _PERCENT_RE.fullmatch(text)
    if match is None:
        raise ValidationError("margen", text, "formato esperado: 15%")
    return int(match[1])


class _Collector:
    """Acumula límites, reglas y problemas mientras se recorre la nota."""

    def __init__(self) -> None:
        self.limits: dict[str, str] = {}
        self.rules: list[ScheduleRule] = []
        self.problems: list[str] = []

    def add_limits(self, number: int, item: str) -> None:
        for key, raw in _FIELD_RE.findall(item):
            if key not in REQUIRED_LIMITS and key not in OPTIONAL_LIMITS:
                self.problems.append(f"línea {number}: campo desconocido '{key}' en Límites")
            elif key in self.limits:
                self.problems.append(f"línea {number}: campo '{key}' duplicado")
            else:
                self.limits[key] = raw.strip()

    def add_rule(self, number: int, kind: RuleKind, item: str) -> None:
        try:
            self.rules.append(_parse_rule(kind, item))
        except ValidationError as error:
            self.problems.append(f"línea {number}: {error}")

    def build_limits(self) -> Limits | None:
        missing = tuple(name for name in REQUIRED_LIMITS if not self.limits.get(name))
        if missing:
            if not self.problems:
                raise HorarioIncompletoError(missing)
            self.problems.extend(f"falta {name} (D-13)" for name in missing)
            return None
        values = {**{k: str(v) + "m" for k, v in OPTIONAL_LIMITS.items()}, **self.limits}
        try:
            return Limits(
                focus_max=_duration("foco_max_dia", values["foco_max_dia"]),
                free_min=_duration("libre_min_dia", values["libre_min_dia"]),
                margin_percent=_percent(values["margen"]),
                block_min=_duration("bloque_minimo", values["bloque_minimo"]),
                block_max=_duration("bloque_maximo", values["bloque_maximo"]),
            )
        except ValidationError as error:
            self.problems.append(str(error))
            return None


def _parse_rule(kind: RuleKind, item: str) -> ScheduleRule:
    fields = _FIELD_RE.findall(item)
    body = _FIELD_RE.sub("", item).strip()
    match = _RULE_RE.fullmatch(body)
    if match is None:
        raise ValidationError("línea", item, "formato esperado: 'días HH:MM–HH:MM · título'")
    vigencia = Vigencia.ACTIVA
    for key, raw in fields:
        if key != "estado":
            raise ValidationError(key, raw.strip(), f"campo '{key}' no admitido en el horario")
        vigencia = Vigencia.parse(raw)
    title = (match["title"] or "").strip()
    if kind is RuleKind.DISPONIBLE and title:
        raise ValidationError("título", title, "la disponibilidad no lleva título")
    days_text = match["days"]
    span = TimeRange.parse(match["range"])
    if _ISO_DATE_RE.fullmatch(days_text):
        try:
            on = date.fromisoformat(days_text)
        except ValueError:
            raise ValidationError("fecha", days_text, "fecha inexistente") from None
        return ScheduleRule(kind=kind, range=span, title=title, on=on, vigencia=vigencia)
    return ScheduleRule(
        kind=kind, range=span, title=title, days=parse_days(days_text), vigencia=vigencia
    )


def _zone(frontmatter: str, problems: list[str]) -> ZoneInfo | None:
    match = _ZONE_RE.search(frontmatter)
    if match is None:
        problems.append("falta 'zona' (IANA) en el frontmatter")
        return None
    try:
        return ZoneInfo(match[1])
    except (ZoneInfoNotFoundError, ValueError):
        problems.append(f"zona '{match[1]}' no es una zona IANA conocida")
        return None


def parse_horario(text: str) -> Schedule:
    """Devuelve el horario validado o lanza `HorarioError` con todos los problemas."""
    lines = _lines(text)
    frontmatter, body_start = _frontmatter(lines)
    collector = _Collector()
    zone = _zone(frontmatter, collector.problems)
    for number, section, item in _items(lines, body_start):
        if section == _LIMITS_SECTION:
            collector.add_limits(number, item)
        elif section in _RULE_SECTIONS:
            collector.add_rule(number, _RULE_SECTIONS[section], item)
    limits = collector.build_limits()
    if collector.problems or zone is None or limits is None:
        raise HorarioError(tuple(collector.problems))
    try:
        return Schedule(zone=zone, limits=limits, rules=tuple(collector.rules))
    except ValidationError as error:
        raise HorarioError((str(error),)) from None
