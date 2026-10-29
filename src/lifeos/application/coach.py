"""Caso de uso del Coach (F4-T01, F4-T02, agente-coach.md): briefing y check-in.

El Coach usa solo sus herramientas registradas (permisos y auditoría en backend, SEC-04): lee
`_sistema/horario.md` y la nota diaria, anexa en `## Check-ins` y crea capturas. Sus
propuestas al Jefe son entradas de check-in (`domain/adjustments.py`). Con IA, el modelo solo
redacta el mensaje que se muestra; si falla, se usa la respuesta determinista.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from zoneinfo import ZoneInfo

from lifeos.application.tasks import ApplicationError
from lifeos.application.tools import ActorContext, ToolRegistry
from lifeos.domain.adjustments import (
    Adjustment,
    AdjustmentKind,
    adjustment_entry,
    parse_adjustments,
    rejected_count,
    same_proposal,
)
from lifeos.domain.checkin import CHECKIN_SECTION
from lifeos.domain.coach import (
    CheckInState,
    CoachDecision,
    MicroStep,
    ReplyKind,
    block_span,
    current_block,
    decide,
    in_protected,
    next_block,
)
from lifeos.domain.horario_note import HORARIO_NOTE, parse_horario
from lifeos.domain.ids import TaskId
from lifeos.domain.plan import Block, BlockKind
from lifeos.domain.plan_note import PlanNoteError, daily_path, is_approved, parse_daily_blocks
from lifeos.domain.proposals import CoachDraft
from lifeos.domain.sections import section_lines
from lifeos.ports.clock import Clock
from lifeos.ports.llm import LlmError
from lifeos.ports.logs import LogWriteError
from lifeos.ports.vault import NoteNotFoundError, VaultPath

COACH_ID = "coach"
# Respuestas cuyo mensaje del código (registro, escalado, descanso) se muestra siempre.
_STATUS_KINDS = frozenset(
    {ReplyKind.REPLANIFICAR, ReplyKind.ALIVIO, ReplyKind.REGISTRO, ReplyKind.DESCANSO}
)


class NoDailyPlanError(ApplicationError):
    """No hay nota diaria para hoy: el Coach no la crea (la publica el Jefe al aprobar)."""


class CoachWriter(Protocol):
    """Redacta el mensaje del Coach (p. ej., con IA). Nunca escribe en la bóveda."""

    def write(self, decision: CoachDecision, *, correlation_id: str) -> CoachDraft: ...


@dataclass(frozen=True, slots=True)
class Briefing:
    day: date
    approved: bool
    blocks: tuple[Block, ...]
    current: Block | None
    upcoming: Block | None
    resting: Block | None
    checkins: int
    adjustments: tuple[Adjustment, ...]
    zone: ZoneInfo
    readable: bool = True  # False si la nota diaria no sigue el formato del plan
    rejected_adjustments: int = 0  # líneas "ajuste:" que no siguen el formato exacto

    @property
    def work(self) -> tuple[Block, ...]:
        return tuple(b for b in self.blocks if b.kind is BlockKind.TRABAJO)


@dataclass(frozen=True, slots=True)
class CheckInOutcome:
    kind: ReplyKind
    message: str
    micro_step: MicroStep | None
    adjustment: Adjustment | None
    path: VaultPath
    capture: VaultPath | None
    ai_fallback: bool = False
    readable: bool = True  # False: la nota de hoy no sigue el formato y no se usaron sus bloques


@dataclass(frozen=True, slots=True)
class _Day:
    day: date
    path: str
    text: str
    zone: ZoneInfo


def schedule_zone(horario: str | None, configured: str | None) -> ZoneInfo:
    """Zona de `_sistema/horario.md`, que debe coincidir con LIFEOS_TIMEZONE si está definida."""
    if horario is None:
        raise ApplicationError(f"falta {HORARIO_NOTE}: el ciclo diario necesita el horario (D-13)")
    zone = parse_horario(horario).zone
    if configured and configured != zone.key:
        raise ApplicationError(
            f"la zona del horario ({zone.key}) no coincide con LIFEOS_TIMEZONE ({configured})"
        )
    return zone


def _count_entries(text: str) -> int:
    lines = section_lines(text, CHECKIN_SECTION) or ()
    return sum(1 for line in lines[1:] if line.startswith("- "))


class CoachSession:
    def __init__(
        self,
        tools: ToolRegistry,
        clock: Clock,
        *,
        writer: CoachWriter | None = None,
        configured_zone: str | None = None,
    ) -> None:
        self._tools = tools
        self._clock = clock
        self._writer = writer
        self._configured_zone = configured_zone

    @property
    def has_writer(self) -> bool:
        return self._writer is not None

    def new_actor(self) -> ActorContext:
        return ActorContext(agent_id=COACH_ID, correlation_id=uuid.uuid4().hex)

    # --- Lectura ----------------------------------------------------------------------------

    def _zone(self, actor: ActorContext) -> ZoneInfo:
        try:
            text = self._tools.leer_nota(actor, HORARIO_NOTE)
        except NoteNotFoundError:
            text = None
        return schedule_zone(text, self._configured_zone)

    def _today(self, actor: ActorContext, day: date | None) -> _Day:
        zone = self._zone(actor)
        target = day or self._clock.now().astimezone(zone).date()
        path = daily_path(target)
        try:
            text = self._tools.leer_nota(actor, path)
        except NoteNotFoundError:
            raise NoDailyPlanError(
                f"no hay plan para {target} en {path}: propón y aprueba la semana "
                "('lifeos semana' y 'lifeos aprobar')"
            ) from None
        return _Day(target, path, text, zone)

    @staticmethod
    def _blocks(today: _Day) -> tuple[tuple[Block, ...], bool]:
        try:
            return parse_daily_blocks(today.text, today.day, today.zone), True
        except PlanNoteError:
            # Nota editada a mano fuera de formato: se registra igual, sin inventar bloques.
            return (), False

    def briefing(self, day: date | None = None) -> Briefing:
        actor = self.new_actor()
        today = self._today(actor, day)
        blocks, readable = self._blocks(today)
        now = self._clock.now()
        return Briefing(
            day=today.day,
            approved=is_approved(today.text),
            blocks=blocks,
            current=current_block(blocks, now),
            upcoming=next_block(blocks, now),
            resting=in_protected(blocks, now),
            checkins=_count_entries(today.text),
            adjustments=parse_adjustments(today.text, today.day),
            zone=today.zone,
            readable=readable,
            rejected_adjustments=rejected_count(today.text),
        )

    # --- Check-in ---------------------------------------------------------------------------

    def checkin(
        self,
        note: str = "",
        *,
        state: CheckInState = CheckInState.NOTA,
        task_id: TaskId | None = None,
        capture: str | None = None,
        use_ai: bool = False,
    ) -> CheckInOutcome:
        if use_ai and self._writer is None:
            raise ApplicationError("el Coach con IA no está configurado (falta la pasarela)")
        actor = self.new_actor()
        today = self._today(actor, None)
        blocks, readable = self._blocks(today)
        decision = decide(
            state,
            now=self._clock.now(),
            zone=today.zone,
            blocks=blocks,
            task_id=task_id,
            note=note,
        )
        # Registro y propuesta en una sola escritura: o quedan los dos o ninguno.
        extra = (adjustment_entry(decision.adjustment),) if decision.adjustment else ()
        path = self._tools.anexar_checkin(actor, today.path, decision.record, *extra)
        captured = self._tools.crear_captura(actor, capture) if capture else None
        message, step, fallback = self._message(decision, actor, use_ai)
        return CheckInOutcome(
            kind=decision.kind,
            message=message,
            micro_step=step,
            adjustment=decision.adjustment,
            path=path,
            capture=captured,
            ai_fallback=fallback,
            readable=readable,
        )

    def _message(
        self, decision: CoachDecision, actor: ActorContext, use_ai: bool
    ) -> tuple[str, MicroStep | None, bool]:
        if not use_ai or self._writer is None:
            return decision.message, decision.micro_step, False
        try:
            draft = self._writer.write(decision, correlation_id=actor.correlation_id)
        except (LlmError, LogWriteError, ApplicationError):
            # El check-in ya está escrito: cualquier fallo del modelo o de su registro de uso
            # se resuelve con la respuesta determinista, nunca con un error que invite a repetir.
            return decision.message, decision.micro_step, True
        # El modelo no puede añadir un micro-paso que las reglas no pidieron.
        step = (draft.micro_step or decision.micro_step) if decision.micro_step else None
        if decision.kind in _STATUS_KINDS:
            # Lo que el código registró y escaló se dice siempre, diga lo que diga el modelo.
            return f"{draft.message} {decision.message}", step, False
        return draft.message, step, False

    # --- Cierre -----------------------------------------------------------------------------

    def record_missed(self, day: date, blocks: Sequence[Block]) -> tuple[Adjustment, ...]:
        """Propone al Jefe reubicar los bloques que el propietario dio por no realizados."""
        actor = self.new_actor()
        today = self._today(actor, day)
        now = self._clock.now().astimezone(today.zone)
        at = now.time().replace(second=0, microsecond=0)
        existing = parse_adjustments(today.text, day)
        recorded: list[Adjustment] = []
        for block in blocks:
            if block.task_id is None:
                continue
            adjustment = Adjustment(
                day,
                at,
                AdjustmentKind.BLOQUE_PERDIDO,
                block_span(block, today.zone),
                block.task_id,
            )
            # Una propuesta ya registrada (p. ej., desde un check-in) no se repite.
            if not any(same_proposal(adjustment, old) for old in (*existing, *recorded)):
                recorded.append(adjustment)
        if recorded:
            first, *rest = (adjustment_entry(a) for a in recorded)
            self._tools.anexar_checkin(actor, today.path, first, *rest)
        return tuple(recorded)
