"""Entidad Task y sus value objects (spec formato-de-tareas, RULES §D3)."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId

MINUTES_PER_HOUR = 60
MAX_PERCENT = 100
_EFFORT_RE = re.compile(r"(\d+(?:\.\d+)?)([mh])")
_PROGRESS_RE = re.compile(r"(\d+)%?")


def _parse_enum[E: StrEnum](enum_cls: type[E], field: str, text: str) -> E:
    try:
        return enum_cls(text.strip().lower())
    except ValueError:
        allowed = ", ".join(member.value for member in enum_cls)
        raise ValidationError(field, text, f"valores permitidos: {allowed}") from None


class Area(StrEnum):
    UNIVERSIDAD = "universidad"
    PROYECTOS = "proyectos"
    CARRERA = "carrera"
    HOBBIES = "hobbies"
    SALUD = "salud"
    FINANZAS = "finanzas"
    COTIDIANO = "cotidiano"

    @classmethod
    def parse(cls, text: str) -> "Area":
        return _parse_enum(cls, "area", text)


class Priority(StrEnum):
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"

    @classmethod
    def parse(cls, text: str) -> "Priority":
        return _parse_enum(cls, "prioridad", text)


class ExplicitState(StrEnum):
    BLOQUEADA = "bloqueada"
    CONFIRMADA = "confirmada"

    @classmethod
    def parse(cls, text: str) -> "ExplicitState":
        return _parse_enum(cls, "estado", text)


class Checkbox(StrEnum):
    PENDING = " "
    DONE = "x"
    CANCELLED = "-"

    @classmethod
    def parse(cls, mark: str) -> "Checkbox":
        try:
            return cls(mark.lower())
        except ValueError:
            raise ValidationError("casilla", mark, "se espera ' ', 'x' o '-'") from None


class TaskStatus(StrEnum):
    PROPUESTA = "propuesta"
    PROGRAMADA = "programada"
    BLOQUEADA = "bloqueada"
    CONFIRMADA = "confirmada"
    COMPLETADA = "completada"
    CANCELADA = "cancelada"


@dataclass(frozen=True, slots=True)
class Effort:
    """Esfuerzo restante en minutos enteros y positivos."""

    minutes: int

    def __post_init__(self) -> None:
        if type(self.minutes) is not int:
            raise ValidationError("esfuerzo", self.minutes, "debe ser un número entero de minutos")
        if self.minutes <= 0:
            raise ValidationError("esfuerzo", self.minutes, "debe ser mayor que cero")

    @classmethod
    def parse(cls, text: str) -> "Effort":
        match = _EFFORT_RE.fullmatch(text)
        if match is None:
            raise ValidationError("esfuerzo", text, "formato esperado: 30m, 1.5h o 4h")
        amount = Decimal(match.group(1))
        minutes = amount * MINUTES_PER_HOUR if match.group(2) == "h" else amount
        if minutes != minutes.to_integral_value():
            raise ValidationError("esfuerzo", text, "debe equivaler a minutos enteros")
        if minutes <= 0:
            raise ValidationError("esfuerzo", text, "debe ser mayor que cero")
        return cls(int(minutes))

    def render(self) -> str:
        hours, rest = divmod(self.minutes, MINUTES_PER_HOUR)
        if rest == 0:
            return f"{hours}h"
        if hours > 0 and rest == MINUTES_PER_HOUR // 2:
            return f"{hours}.5h"
        return f"{self.minutes}m"


@dataclass(frozen=True, slots=True)
class Progress:
    """Avance en porcentaje entero entre 0 y 100."""

    percent: int

    def __post_init__(self) -> None:
        if type(self.percent) is not int:
            raise ValidationError("avance", self.percent, "debe ser un porcentaje entero")
        if not 0 <= self.percent <= MAX_PERCENT:
            raise ValidationError("avance", self.percent, "debe estar entre 0 y 100")

    @classmethod
    def parse(cls, text: str) -> "Progress":
        match = _PROGRESS_RE.fullmatch(text.strip())
        if match is None:
            raise ValidationError("avance", text, "formato esperado: 25%")
        return cls(int(match.group(1)))

    def render(self) -> str:
        return f"{self.percent}%"


@dataclass(frozen=True, slots=True)
class Task:
    """Tarea de la bóveda. Inmutable: los cambios crean una tarea nueva."""

    title: str
    checkbox: Checkbox
    id: TaskId | None = None
    area: Area | None = None
    effort: Effort | None = None
    progress: Progress | None = None
    priority: Priority = Priority.MEDIA
    due: date | None = None
    scheduled: date | None = None
    recurrence: str | None = None
    state: ExplicitState | None = None
    inferred: bool = False
    # Planificación (planificador.md): tareas que deben terminar antes y si admite partirse.
    depends_on: tuple[TaskId, ...] = ()
    divisible: bool = True

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValidationError("título", self.title, "no puede estar vacío")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValidationError("depende", self.depends_on, "dependencia repetida")
        if self.id is not None and self.id in self.depends_on:
            raise ValidationError("depende", self.id.value, "una tarea no depende de sí misma")

    @property
    def status(self) -> TaskStatus:
        if self.checkbox is Checkbox.DONE:
            return TaskStatus.COMPLETADA
        if self.checkbox is Checkbox.CANCELLED:
            return TaskStatus.CANCELADA
        if self.state is not None:
            return TaskStatus(self.state.value)
        if self.scheduled is not None:
            return TaskStatus.PROGRAMADA
        return TaskStatus.PROPUESTA

    def missing_required_fields(self) -> tuple[str, ...]:
        """Campos obligatorios del spec ausentes (las notas escritas a mano pueden omitirlos)."""
        missing = []
        if self.area is None:
            missing.append("area")
        if self.effort is None:
            missing.append("esfuerzo")
        return tuple(missing)
