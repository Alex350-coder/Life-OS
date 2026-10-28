"""Plan semanal: bloques, propuesta, conflictos y contexto de planificación (planificador.md).

Los compromisos fijos y el tiempo protegido salen del horario, nunca de una propuesta: el
validador exige que el plan los contenga exactamente.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from zoneinfo import ZoneInfo

from lifeos.domain.capacity import DayCapacity, Interval, week_capacity
from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.schedule import Limits, RuleKind, Schedule
from lifeos.domain.task import Task, TaskStatus
from lifeos.domain.weeks import IsoWeek

# Estados con trabajo pendiente que el Jefe puede programar (RULES §D3).
PLANNABLE = frozenset({TaskStatus.PROPUESTA, TaskStatus.PROGRAMADA, TaskStatus.CONFIRMADA})
MAX_TITLE_CHARS = 200


def clean(text: str, limit: int = MAX_TITLE_CHARS) -> str:
    """Una sola línea, sin corchetes y con longitud acotada: el texto queda inerte (SEC-10).

    Los títulos de los bloques se normalizan al construirlos, de modo que el plan en memoria,
    la nota escrita y el plan releído al aprobar son idénticos.
    """
    single = " ".join(text.replace("[", "(").replace("]", ")").split())
    return single if len(single) <= limit else single[: limit - 1] + "…"


class BlockKind(StrEnum):
    TRABAJO = "trabajo"
    FIJO = "fijo"
    PROTEGIDO = "protegido"


class PlanOrigin(StrEnum):
    HEURISTICA = "heuristica"
    IA = "ia"


class PlanStatus(StrEnum):
    PROPUESTO = "propuesto"
    APROBADO = "aprobado"


@dataclass(frozen=True, slots=True)
class Block:
    kind: BlockKind
    title: str
    interval: Interval
    task_id: TaskId | None = None

    def __post_init__(self) -> None:
        if (self.kind is BlockKind.TRABAJO) != (self.task_id is not None):
            raise ValidationError("bloque", self.title, "solo los bloques de trabajo llevan tarea")
        if not self.title.strip():
            raise ValidationError("bloque", self.title, "el título no puede estar vacío")
        object.__setattr__(self, "title", clean(self.title))


class UnplannedCode(StrEnum):
    SIN_ID = "sin_id"
    FALTA_INFORMACION = "falta_informacion"
    DEPENDENCIA = "dependencia"
    DEPENDENCIA_CIRCULAR = "dependencia_circular"
    NO_DIVISIBLE = "no_divisible"
    SIN_CAPACIDAD = "sin_capacidad"
    NO_CABE_ENTERA = "no_cabe_entera"


@dataclass(frozen=True, slots=True)
class Unplanned:
    """Tarea (o resto de tarea) que no cabe, con motivo y propuesta de aplazamiento."""

    task_id: TaskId | None
    title: str
    minutes: int
    code: UnplannedCode
    reason: str
    suggestion: str


@dataclass(frozen=True, slots=True)
class PlanProposal:
    week: IsoWeek
    blocks: tuple[Block, ...]
    unplanned: tuple[Unplanned, ...] = ()
    justification: str = ""
    origin: PlanOrigin = PlanOrigin.HEURISTICA
    alerts: tuple[str, ...] = ()

    def work_blocks(self) -> tuple[Block, ...]:
        return tuple(block for block in self.blocks if block.kind is BlockKind.TRABAJO)


class ConflictCode(StrEnum):
    SOLAPAMIENTO = "solapamiento"
    FUERA_DE_SEMANA = "fuera_de_semana"
    CRUZA_DIA = "cruza_dia"
    FECHA_PASADA = "fecha_pasada"
    FUERA_DE_DISPONIBILIDAD = "fuera_de_disponibilidad"
    TIEMPO_PROTEGIDO = "tiempo_protegido"
    COMPROMISO_FIJO = "compromiso_fijo"
    FIJO_ALTERADO = "fijo_alterado"
    PROTEGIDO_ELIMINADO = "protegido_eliminado"
    EXCESO_DE_CARGA = "exceso_de_carga"
    TAREA_DESCONOCIDA = "tarea_desconocida"
    TAREA_NO_PLANIFICABLE = "tarea_no_planificable"
    EXCEDE_ESFUERZO = "excede_esfuerzo"
    BLOQUE_CORTO = "bloque_corto"
    BLOQUE_LARGO = "bloque_largo"
    NO_DIVISIBLE_PARTIDA = "no_divisible_partida"
    DEPENDENCIA = "dependencia"
    DESPUES_DE_FECHA_LIMITE = "despues_de_fecha_limite"


@dataclass(frozen=True, slots=True)
class Conflict:
    code: ConflictCode
    message: str
    day: date | None = None


@dataclass(frozen=True, slots=True, eq=False)
class PlanningContext:
    """Todo lo que el validador necesita, calculado por código antes de proponer."""

    week: IsoWeek
    zone: ZoneInfo
    limits: Limits
    days: tuple[DayCapacity, ...]
    tasks: Mapping[TaskId, Task]
    # Tareas sin id o sin campos obligatorios: no se pueden planificar y se informan.
    incomplete: tuple[Task, ...]
    not_before: datetime

    def __post_init__(self) -> None:
        # Copia de solo lectura: nadie puede cambiar las tareas tras validar.
        object.__setattr__(self, "tasks", MappingProxyType(dict(self.tasks)))

    def day(self, day: date) -> DayCapacity | None:
        return next((capacity for capacity in self.days if capacity.day == day), None)

    def local_day(self, moment: datetime) -> date:
        return moment.astimezone(self.zone).date()


def next_minute(moment: datetime) -> datetime:
    """Primer minuto entero no anterior a `moment`: los bloques siempre empiezan en HH:MM."""
    whole = moment.replace(second=0, microsecond=0)
    return whole if whole == moment else whole + timedelta(minutes=1)


def build_context(
    schedule: Schedule, week: IsoWeek, tasks: Iterable[Task], *, now: datetime
) -> PlanningContext:
    if now.utcoffset() is None:
        raise ValidationError("ahora", now, "requiere zona horaria")
    now = next_minute(now)
    by_id: dict[TaskId, Task] = {}
    incomplete: list[Task] = []
    for task in tasks:
        if task.id is None:
            if task.status in PLANNABLE:
                incomplete.append(task)
            continue
        by_id[task.id] = task
    return PlanningContext(
        week=week,
        zone=schedule.zone,
        limits=schedule.limits,
        days=week_capacity(schedule, week, not_before=now),
        tasks=MappingProxyType(by_id),
        incomplete=tuple(incomplete),
        not_before=now,
    )


_KIND_OF_RULE = {RuleKind.FIJO: BlockKind.FIJO, RuleKind.PROTEGIDO: BlockKind.PROTEGIDO}


def fixed_and_protected_blocks(context: PlanningContext) -> tuple[Block, ...]:
    """Bloques que el plan debe contener tal cual: los pone el código, no el modelo."""
    return tuple(
        Block(_KIND_OF_RULE[event.kind], event.title, event.interval)
        for day in context.days
        for event in sorted((*day.fixed, *day.protected), key=lambda e: e.interval.start)
    )
