"""Reglas deterministas del Coach (F4-T01, F4-T02, agente-coach.md).

El Coach decide el tono, el micro-paso y cuándo escalar una desviación al Jefe. No decide el
plan ni cambia tareas: un "hecho" solo se registra y se confirma en el cierre, y el silencio
nunca cuenta como cumplimiento. Durante el tiempo protegido no empuja a trabajar.
"""

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

from lifeos.domain.adjustments import Adjustment, AdjustmentKind
from lifeos.domain.checkin import MAX_CHECKIN_CHARS, CheckInEntry
from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.plan import Block, BlockKind, clean
from lifeos.domain.schedule import TimeRange

MAX_MICRO_STEP_MINUTES = 10
MAX_MICRO_STEP_CHARS = 120
MAX_TITLE_IN_MESSAGE = 60
NOTE_PREFIX = "nota: "
MINUTES_PER_HOUR = 60
_ALLOWED_SPACES = frozenset(" \t")
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


class CheckInState(StrEnum):
    EMPEZANDO = "empezando"
    BLOQUEADO = "bloqueado"
    CANSADO = "cansado"
    HECHO = "hecho"
    PERDIDO = "perdido"
    NOTA = "nota"


class ReplyKind(StrEnum):
    ANIMO = "animo"
    MICRO_PASO = "micro_paso"
    ALIVIO = "alivio"
    REGISTRO = "registro"
    REPLANIFICAR = "replanificar"
    DESCANSO = "descanso"


@dataclass(frozen=True, slots=True)
class MicroStep:
    """Acción concreta de 1 a 10 minutos para vencer el bloqueo inicial."""

    action: str
    minutes: int

    def __post_init__(self) -> None:
        if type(self.minutes) is not int or not 1 <= self.minutes <= MAX_MICRO_STEP_MINUTES:
            raise ValidationError(
                "micro-paso", self.minutes, f"debe durar de 1 a {MAX_MICRO_STEP_MINUTES} minutos"
            )
        action = clean(self.action, MAX_MICRO_STEP_CHARS)
        if not action:
            raise ValidationError("micro-paso", "", "la acción no puede estar vacía")
        object.__setattr__(self, "action", action)


@dataclass(frozen=True, slots=True)
class CoachDecision:
    kind: ReplyKind
    message: str
    record: CheckInEntry
    micro_step: MicroStep | None = None
    adjustment: Adjustment | None = None
    block: Block | None = None


# --- Bloques del día --------------------------------------------------------------------------


def _work(blocks: Iterable[Block]) -> list[Block]:
    chosen = [b for b in blocks if b.kind is BlockKind.TRABAJO]
    return sorted(chosen, key=lambda b: b.interval.start.timestamp())


def _contains(block: Block, now: datetime) -> bool:
    return block.interval.start.timestamp() <= now.timestamp() < block.interval.end.timestamp()


def current_block(blocks: Iterable[Block], now: datetime) -> Block | None:
    return next((b for b in _work(blocks) if _contains(b, now)), None)


def next_block(blocks: Iterable[Block], now: datetime) -> Block | None:
    return next((b for b in _work(blocks) if b.interval.start.timestamp() > now.timestamp()), None)


def in_protected(blocks: Iterable[Block], now: datetime) -> Block | None:
    return next(
        (b for b in blocks if b.kind is BlockKind.PROTEGIDO and _contains(b, now)),
        None,
    )


def _block_of(task_id: TaskId, blocks: Sequence[Block], now: datetime, *, missed: bool) -> Block:
    own = [b for b in _work(blocks) if b.task_id == task_id]
    if not own:
        raise ValidationError("tarea", str(task_id), "no tiene bloques en el plan de hoy")
    started = [b for b in own if b.interval.start.timestamp() <= now.timestamp()]
    if missed and not started:
        raise ValidationError("tarea", str(task_id), "su bloque de hoy aún no empezó")
    return started[-1] if started else own[0]


def _missed_block(blocks: Sequence[Block], now: datetime) -> Block:
    """Sin tarea indicada: el bloque en curso o, si no hay, el último que ya empezó."""
    current = current_block(blocks, now)
    if current is not None:
        return current
    started = [b for b in _work(blocks) if b.interval.start.timestamp() <= now.timestamp()]
    if not started:
        raise ValidationError(
            "tarea", "", "no hay un bloque de hoy que dar por perdido: usa --tarea"
        )
    return started[-1]


# --- Mensajes ---------------------------------------------------------------------------------


def _title(block: Block | None) -> str:
    return clean(block.title, MAX_TITLE_IN_MESSAGE) if block is not None else ""


def block_span(block: Block, zone: ZoneInfo) -> TimeRange:
    """Horario local `HH:MM–HH:MM` del bloque (la medianoche siguiente es 24:00)."""
    start = block.interval.start.astimezone(zone)
    end = block.interval.end.astimezone(zone)
    end_minutes = end.hour * MINUTES_PER_HOUR + end.minute
    if end.date() > start.date() and end_minutes == 0:
        end_minutes = 24 * MINUTES_PER_HOUR
    return TimeRange(start.hour * MINUTES_PER_HOUR + start.minute, end_minutes)


def micro_step_for(block: Block | None) -> MicroStep:
    if block is None:
        return MicroStep(
            "Elige la tarea más pequeña de hoy y trabaja en ella solo 10 minutos",
            MAX_MICRO_STEP_MINUTES,
        )
    return MicroStep(
        f"Abre lo necesario para «{_title(block)}» y escribe el primer paso concreto",
        MAX_MICRO_STEP_MINUTES,
    )


def _single_line(note: str) -> str:
    """Rechaza saltos de línea Unicode y caracteres de control en lugar de unirlos en silencio."""
    for char in note:
        if char in _ALLOWED_SPACES:
            continue
        if char.isspace() or unicodedata.category(char) in _INVISIBLE_CATEGORIES:
            raise ValidationError("nota", "…", "contiene saltos o caracteres de control")
    return " ".join(note.split())


def _record(state: CheckInState, task_id: TaskId | None, note: str, at: time) -> CheckInEntry:
    head = f"estado: {state.value}" + (f" · {task_id}" if task_id is not None else "")
    text = _single_line(note)
    if text:
        room = MAX_CHECKIN_CHARS - len(head) - len(" · ") - len(NOTE_PREFIX)
        cut = text if len(text) <= room else text[: room - 1] + "…"
        head = f"{head} · {NOTE_PREFIX}{cut}"
    return CheckInEntry(at, head)


def _resting(state: CheckInState, rest: Block) -> str:
    title = _title(rest)
    if state is CheckInState.BLOQUEADO:
        return f"Ahora es tiempo protegido («{title}»): descansa sin culpa; lo retomamos después."
    return f"Ahora es tiempo protegido («{title}»). Lo anoto; no hace falta trabajar ahora."


@dataclass(frozen=True, slots=True)
class _Reply:
    kind: ReplyKind
    message: str
    micro_step: MicroStep | None = None
    adjustment: Adjustment | None = None


def _reply(
    state: CheckInState, block: Block | None, now: datetime, zone: ZoneInfo, rest: Block | None
) -> _Reply:
    local = now.astimezone(zone)
    at = time(local.hour, local.minute)
    if state is CheckInState.CANSADO:
        relief = Adjustment(local.date(), at, AdjustmentKind.REDUCIR_HOY)
        return _Reply(
            ReplyKind.ALIVIO,
            "Gracias por avisar. Propongo al Jefe reducir la carga de hoy; tú decides al "
            "aprobar la revisión. Tómate un respiro.",
            adjustment=relief,
        )
    if state is CheckInState.PERDIDO and block is not None and block.task_id is not None:
        span = block_span(block, zone)
        missed = Adjustment(local.date(), at, AdjustmentKind.BLOQUE_PERDIDO, span, block.task_id)
        return _Reply(
            ReplyKind.REPLANIFICAR,
            f"Queda anotado que el bloque {span.render()} de «{_title(block)}» no se hizo. "
            "Pido al Jefe reubicarlo: revisa la propuesta con 'lifeos semana --replanificar'.",
            adjustment=missed,
        )
    if rest is not None:
        return _Reply(ReplyKind.DESCANSO, _resting(state, rest))
    if state is CheckInState.BLOQUEADO:
        step = micro_step_for(block)
        return _Reply(
            ReplyKind.MICRO_PASO,
            f"Cuesta empezar y es normal. Prueba solo esto ({step.minutes} min): {step.action}.",
            micro_step=step,
        )
    if state is CheckInState.HECHO:
        return _Reply(
            ReplyKind.REGISTRO,
            "¡Bien! Lo anoto. Confirma el avance en el cierre del día ('lifeos cierre').",
        )
    if state is CheckInState.EMPEZANDO:
        target = f" con «{_title(block)}»" if block is not None else ""
        return _Reply(ReplyKind.ANIMO, f"Adelante{target}. Un paso cada vez.")
    return _Reply(ReplyKind.REGISTRO, "Anotado.")


def decide(
    state: CheckInState,
    *,
    now: datetime,
    zone: ZoneInfo,
    blocks: Sequence[Block],
    task_id: TaskId | None = None,
    note: str = "",
) -> CoachDecision:
    """Respuesta, registro y propuesta (si corresponde) para un check-in.

    Solo cuentan los bloques del día local de `now`; un bloque perdido debe haber empezado.
    """
    if now.utcoffset() is None:
        raise ValidationError("ahora", now, "requiere zona horaria")
    local = now.astimezone(zone)
    blocks = [b for b in blocks if b.interval.start.astimezone(zone).date() == local.date()]
    missed = state is CheckInState.PERDIDO
    if task_id is not None:
        block: Block | None = _block_of(task_id, blocks, now, missed=missed)
    elif missed:
        block = _missed_block(blocks, now)
    else:
        block = current_block(blocks, now)
    if task_id is None and state is CheckInState.PERDIDO and block is not None:
        task_id = block.task_id  # el registro dice qué bloque se dio por perdido
    record = _record(state, task_id, note, time(local.hour, local.minute))
    reply = _reply(state, block, now, zone, in_protected(blocks, now))
    return CoachDecision(
        kind=reply.kind,
        message=reply.message,
        record=record,
        micro_step=reply.micro_step,
        adjustment=reply.adjustment,
        block=block,
    )
