"""Horario y límites del propietario (spec horario-y-limites, D-13, D-14).

Las reglas se expresan en hora local de la zona del horario; `capacity.py` las convierte en
instantes reales. Solo cuentan las reglas `activa`: las sustituidas o pendientes de confirmar se
conservan como historial (spec memoria-y-vigencia).
"""

import re
from dataclasses import dataclass
from datetime import date
from enum import IntEnum, StrEnum
from itertools import combinations
from zoneinfo import ZoneInfo

from lifeos.domain.errors import ValidationError

MINUTES_PER_DAY = 24 * 60
MAX_PERCENT = 100
DEFAULT_BLOCK_MIN = 30
DEFAULT_BLOCK_MAX = 120
WHOLE_DAY = "todo el día"
_HHMM = r"(\d{2}):(\d{2})"
_RANGE_RE = re.compile(rf"{_HHMM}\s*[–-]\s*{_HHMM}")


class Weekday(IntEnum):
    LUN = 0
    MAR = 1
    MIE = 2
    JUE = 3
    VIE = 4
    SAB = 5
    DOM = 6

    @classmethod
    def parse(cls, text: str) -> "Weekday":
        try:
            return _DAY_NAMES[text.strip().casefold()]
        except KeyError:
            raise ValidationError(
                "día", text, "se espera lun, mar, mié, jue, vie, sáb o dom"
            ) from None

    @classmethod
    def of(cls, day: date) -> "Weekday":
        return cls(day.weekday())

    def render(self) -> str:
        return _DAY_LABELS[self]


_DAY_LABELS = {
    Weekday.LUN: "lun",
    Weekday.MAR: "mar",
    Weekday.MIE: "mié",
    Weekday.JUE: "jue",
    Weekday.VIE: "vie",
    Weekday.SAB: "sáb",
    Weekday.DOM: "dom",
}
_DAY_NAMES = {label: day for day, label in _DAY_LABELS.items()} | {
    "mie": Weekday.MIE,
    "sab": Weekday.SAB,
}


def parse_days(text: str) -> frozenset[Weekday]:
    """`lun`, `lun,jue` o `lun-vie` (sin dar la vuelta a la semana)."""
    days: set[Weekday] = set()
    for part in text.split(","):
        bounds = part.split("-")
        if len(bounds) > 2:  # inicio y fin de un rango
            raise ValidationError("días", text, "un rango tiene la forma lun-vie")
        first, last = Weekday.parse(bounds[0]), Weekday.parse(bounds[-1])
        if first > last:
            raise ValidationError("días", text, "el rango debe ir de lunes a domingo")
        days.update(Weekday(value) for value in range(first, last + 1))
    return frozenset(days)


def _minutes(text_hours: str, text_minutes: str, original: str) -> int:
    hours, minutes = int(text_hours), int(text_minutes)
    if minutes >= 60 or hours > 24 or (hours == 24 and minutes > 0):
        raise ValidationError("hora", original, "hora inexistente")
    return hours * 60 + minutes


@dataclass(frozen=True, slots=True)
class TimeRange:
    """Intervalo local semiabierto [start, end) en minutos desde la medianoche."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end <= MINUTES_PER_DAY:
            raise ValidationError(
                "rango", self.render(), "duración cero o negativa, o fuera del día"
            )

    @classmethod
    def parse(cls, text: str) -> "TimeRange":
        clean = text.strip()
        if clean.casefold() == WHOLE_DAY:
            return cls(0, MINUTES_PER_DAY)
        match = _RANGE_RE.fullmatch(clean)
        if match is None:
            raise ValidationError("rango", text, "formato esperado: HH:MM–HH:MM o 'todo el día'")
        start = _minutes(match[1], match[2], clean)
        end = _minutes(match[3], match[4], clean)
        if start >= end:
            raise ValidationError("rango", clean, "duración cero o negativa")
        return cls(start, end)

    @property
    def minutes(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "TimeRange") -> bool:
        return self.start < other.end and other.start < self.end

    def render(self) -> str:
        return f"{_hhmm(self.start)}–{_hhmm(self.end)}"


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass(frozen=True, slots=True)
class Limits:
    """Límites diarios en minutos. `margin_percent` reserva parte del tiempo libre para
    imprevistos; `free_min` es el tiempo libre que debe quedar sin trabajo cada día."""

    focus_max: int
    free_min: int
    margin_percent: int
    block_min: int = DEFAULT_BLOCK_MIN
    block_max: int = DEFAULT_BLOCK_MAX

    def __post_init__(self) -> None:
        for name in ("focus_max", "free_min", "margin_percent", "block_min", "block_max"):
            if type(getattr(self, name)) is not int:
                raise ValidationError(name, getattr(self, name), "debe ser un entero")
        if not 0 < self.focus_max <= MINUTES_PER_DAY:
            raise ValidationError("foco_max_dia", self.focus_max, "debe estar entre 1m y 24h")
        if not 0 <= self.free_min <= MINUTES_PER_DAY:
            raise ValidationError("libre_min_dia", self.free_min, "debe estar entre 0m y 24h")
        if not 0 <= self.margin_percent < MAX_PERCENT:
            raise ValidationError("margen", self.margin_percent, "debe estar entre 0 % y 99 %")
        if not 0 < self.block_min <= self.block_max <= MINUTES_PER_DAY:
            raise ValidationError(
                "bloque_minimo", self.block_min, "debe ser positivo y no mayor que bloque_maximo"
            )


class RuleKind(StrEnum):
    DISPONIBLE = "disponible"
    FIJO = "fijo"
    PROTEGIDO = "protegido"
    NO_DISPONIBLE = "no_disponible"


class Vigencia(StrEnum):
    ACTIVA = "activa"
    SUSTITUIDA = "sustituida"
    PENDIENTE_CONFIRMACION = "pendiente_confirmacion"

    @classmethod
    def parse(cls, text: str) -> "Vigencia":
        try:
            return cls(text.strip().lower())
        except ValueError:
            allowed = ", ".join(member.value for member in cls)
            raise ValidationError("estado", text, f"valores permitidos: {allowed}") from None


TITLED_KINDS = frozenset({RuleKind.FIJO, RuleKind.PROTEGIDO, RuleKind.NO_DISPONIBLE})


@dataclass(frozen=True, slots=True)
class ScheduleRule:
    """Regla semanal (`days`) o de una fecha concreta (`on`), nunca ambas."""

    kind: RuleKind
    range: TimeRange
    title: str = ""
    days: frozenset[Weekday] = frozenset()
    on: date | None = None
    vigencia: Vigencia = Vigencia.ACTIVA

    def __post_init__(self) -> None:
        if bool(self.days) == (self.on is not None):
            raise ValidationError("días", self.title, "indica días de la semana o una fecha")
        if self.kind in TITLED_KINDS and not self.title.strip():
            raise ValidationError("título", self.title, f"obligatorio en '{self.kind}'")

    @property
    def active(self) -> bool:
        return self.vigencia is Vigencia.ACTIVA

    def applies_to(self, day: date) -> bool:
        return day == self.on if self.on is not None else Weekday.of(day) in self.days

    def _weekdays(self) -> frozenset[Weekday]:
        return frozenset({Weekday.of(self.on)}) if self.on is not None else self.days

    def shares_a_day(self, other: "ScheduleRule") -> bool:
        if self.on is not None and other.on is not None:
            return self.on == other.on
        return bool(self._weekdays() & other._weekdays())


@dataclass(frozen=True, slots=True)
class Schedule:
    zone: ZoneInfo
    limits: Limits
    rules: tuple[ScheduleRule, ...]

    def __post_init__(self) -> None:
        blocking = [
            rule
            for rule in self.rules
            if rule.active and rule.kind in (RuleKind.FIJO, RuleKind.PROTEGIDO)
        ]
        for first, second in combinations(blocking, 2):
            if first.shares_a_day(second) and first.range.overlaps(second.range):
                raise ValidationError(
                    "horario",
                    f"{first.title} / {second.title}",
                    "compromisos fijos o tiempo protegido que se solapan",
                )

    def rules_for(self, day: date, kind: RuleKind) -> tuple[ScheduleRule, ...]:
        """Reglas activas de un tipo que se aplican ese día, ordenadas por hora de inicio."""
        return tuple(
            sorted(
                (r for r in self.rules if r.kind is kind and r.active and r.applies_to(day)),
                key=lambda rule: rule.range.start,
            )
        )
