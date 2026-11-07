"""Rituales programados de `_sistema/rituales.md` (spec rituales-y-notificaciones, ADR-0014).

El formato es regular y se analiza sin LLM. Se informan todos los problemas a la vez; una línea
mal formada nunca se ignora. Las horas son locales de la zona del horario.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from enum import StrEnum
from types import MappingProxyType

from lifeos.domain.capacity import local_datetime
from lifeos.domain.errors import DomainError, ValidationError
from lifeos.domain.plan import Block, BlockKind
from lifeos.domain.schedule import MINUTES_PER_DAY, TimeRange, Weekday, parse_days

RITUALES_NOTE = "_sistema/rituales.md"
MAX_REMINDER_LEAD = 120  # minutos
MINUTES_PER_HOUR = 60
_SECTION_LEVEL = 2  # las secciones son encabezados "## "


class RitualKind(StrEnum):
    BRIEFING = "briefing"
    CIERRE = "cierre"
    PROPUESTA_SEMANAL = "propuesta_semanal"
    RECORDATORIO = "recordatorio"


# Margen de recuperación de cada ritual con hora (minutos): pasado el margen, se omite.
GRACE_MINUTES: Mapping[RitualKind, int] = MappingProxyType(
    {RitualKind.BRIEFING: 180, RitualKind.CIERRE: 120, RitualKind.PROPUESTA_SEMANAL: 1440}
)
_RITUALS_SECTION = "rituales"
_QUIET_SECTION = "no molestar"
_HEADING_RE = re.compile(r"(#{1,6})\s+(.*?)\s*#*\s*")
_ITEM_RE = re.compile(r"\s*[-*+]\s+(.*)")
_ENTRY_RE = re.compile(r"(?P<name>\S+)\s*·\s*(?P<rest>.+)")
_TIMED_RE = re.compile(r"(?P<days>\S+)\s+(?P<hh>\d{2}):(?P<mm>\d{2})")
_LEAD_RE = re.compile(r"(\d+)m antes")
_QUIET_RE = re.compile(r"(?P<days>\S+)\s+(?P<range>\d{2}:\d{2}\s*[–-]\s*\d{2}:\d{2})")
_REMINDER_OFF = "desactivado"


class RitualesError(DomainError):
    """`rituales.md` no se puede usar; `problems` enumera todos los motivos con su línea."""

    def __init__(self, problems: tuple[str, ...]) -> None:
        super().__init__("rituales.md no válido: " + "; ".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class TimedRitual:
    kind: RitualKind
    days: frozenset[Weekday]
    minute: int  # minutos desde la medianoche local


@dataclass(frozen=True, slots=True)
class QuietRule:
    days: frozenset[Weekday]
    range: TimeRange


@dataclass(frozen=True, slots=True)
class RitualConfig:
    timed: tuple[TimedRitual, ...]
    reminder_lead: int | None  # None: recordatorios desactivados
    quiet: tuple[QuietRule, ...]

    def timed_for(self, day: date) -> tuple[TimedRitual, ...]:
        weekday = Weekday.of(day)
        rules = (rule for rule in self.timed if weekday in rule.days)
        return tuple(sorted(rules, key=lambda rule: rule.minute))

    def quiet_for(self, day: date) -> tuple[TimeRange, ...]:
        weekday = Weekday.of(day)
        return tuple(rule.range for rule in self.quiet if weekday in rule.days)


@dataclass(frozen=True, slots=True)
class Occurrence:
    """Una ejecución concreta de un ritual, con una clave estable para no repetirla."""

    kind: RitualKind
    key: str
    day: date
    at: datetime
    deadline: datetime  # pasado este instante, la ocurrencia se omite
    block: Block | None = None  # bloque de trabajo de un recordatorio


def _items(text: str) -> Iterator[tuple[int, str, str]]:
    section: str | None = None
    in_comment = False
    for number, line in enumerate(text.removeprefix("﻿").splitlines(), start=1):
        if in_comment or line.lstrip().startswith("<!--"):
            in_comment = "-->" not in line
            continue
        heading = _HEADING_RE.fullmatch(line)
        if heading:
            section = heading[2].casefold() if len(heading[1]) == _SECTION_LEVEL else None
            continue
        item = _ITEM_RE.fullmatch(line)
        if item and section in (_RITUALS_SECTION, _QUIET_SECTION):
            yield number, section, item[1].strip()


class _Collector:
    def __init__(self) -> None:
        self.timed: list[TimedRitual] = []
        self.lead: int | None = None
        self.lead_seen = False
        self.quiet: list[QuietRule] = []
        self.problems: list[str] = []

    def ritual(self, item: str) -> None:
        entry = _ENTRY_RE.fullmatch(item)
        if entry is None:
            raise ValidationError("ritual", item, "formato esperado: nombre · días HH:MM")
        name, rest = entry["name"], entry["rest"].strip()
        try:
            kind = RitualKind(name)
        except ValueError:
            raise ValidationError("ritual", name, "ritual desconocido") from None
        if kind is RitualKind.RECORDATORIO:
            self._reminder(rest)
        else:
            self._timed(kind, rest)

    def _reminder(self, rest: str) -> None:
        if self.lead_seen:
            raise ValidationError("recordatorio", rest, "el recordatorio se declara una sola vez")
        self.lead_seen = True
        if rest == _REMINDER_OFF:
            return
        match = _LEAD_RE.fullmatch(rest)
        lead = int(match[1]) if match else 0
        if not 0 < lead <= MAX_REMINDER_LEAD:
            raise ValidationError(
                "antelación", rest, f"formato esperado: 5m antes (1 a {MAX_REMINDER_LEAD} min)"
            )
        self.lead = lead

    def _timed(self, kind: RitualKind, rest: str) -> None:
        match = _TIMED_RE.fullmatch(rest)
        if match is None:
            raise ValidationError("ritual", rest, "formato esperado: días HH:MM")
        days = parse_days(match["days"])
        minute = int(match["hh"]) * MINUTES_PER_HOUR + int(match["mm"])
        if int(match["mm"]) >= MINUTES_PER_HOUR or minute >= MINUTES_PER_DAY:
            raise ValidationError("hora", f"{match['hh']}:{match['mm']}", "hora inexistente")
        for other in self.timed:
            if other.kind is kind and other.days & days:
                raise ValidationError(kind.value, match["days"], "repite un día del mismo ritual")
        self.timed.append(TimedRitual(kind, days, minute))

    def quiet_rule(self, item: str) -> None:
        match = _QUIET_RE.fullmatch(item)
        if match is None:
            raise ValidationError("no molestar", item, "formato esperado: días HH:MM–HH:MM")
        self.quiet.append(QuietRule(parse_days(match["days"]), TimeRange.parse(match["range"])))


def parse_rituales(text: str) -> RitualConfig:
    collector = _Collector()
    for number, section, item in _items(text):
        try:
            if section == _RITUALS_SECTION:
                collector.ritual(item)
            else:
                collector.quiet_rule(item)
        except ValidationError as error:
            collector.problems.append(f"línea {number}: {error}")
    if collector.problems:
        raise RitualesError(tuple(collector.problems))
    return RitualConfig(tuple(collector.timed), collector.lead, tuple(collector.quiet))


def ritual_occurrences(config: RitualConfig, day: date, zone: tzinfo) -> tuple[Occurrence, ...]:
    occurrences = []
    for rule in config.timed_for(day):
        try:
            moment = local_datetime(day, rule.minute, zone)
        except ValidationError:
            continue  # la hora no existe o es ambigua ese día por el cambio de horario
        occurrences.append(
            Occurrence(
                kind=rule.kind,
                key=f"{rule.kind.value}:{day.isoformat()}",
                day=day,
                at=moment,
                deadline=moment + timedelta(minutes=GRACE_MINUTES[rule.kind]),
            )
        )
    return tuple(occurrences)


def reminder_occurrences(
    config: RitualConfig, day: date, blocks: Sequence[Block]
) -> tuple[Occurrence, ...]:
    if config.reminder_lead is None:
        return ()
    lead = timedelta(minutes=config.reminder_lead)
    return tuple(
        Occurrence(
            kind=RitualKind.RECORDATORIO,
            key=f"recordatorio:{day.isoformat()}:{block.task_id}:{block.interval.start:%H:%M}",
            day=day,
            at=block.interval.start - lead,
            deadline=block.interval.start,
            block=block,
        )
        for block in blocks
        if block.kind is BlockKind.TRABAJO
    )
