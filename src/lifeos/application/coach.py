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
from lifeos.domain.plan_note import PlanNoteError, daily_path, parse_daily_blocks
from lifeos.domain.proposals import CoachDraft
from lifeos.domain.sections import section_lines
from lifeos.ports.clock import Clock
from lifeos.ports.llm import LlmError
from lifeos.ports.vault import NoteNotFoundError, VaultPath

COACH_ID = "coach"
_APPROVED = "estado_plan: aprobado"


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


@dataclass(frozen=True, slots=True)
class _Day:
    day: date
    path: str
    text: str
    zone: ZoneInfo


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
            raise ApplicationError(
                f"falta {HORARIO_NOTE}: el Coach necesita el horario y su zona (D-13)"
            ) from None
        zone = parse_horario(text).zone
        if self._configured_zone and self._configured_zone != zone.key:
            raise ApplicationError(
                f"la zona del horario ({zone.key}) no coincide con LIFEOS_TIMEZONE "
                f"({self._configured_zone})"
            )
        return zone

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
            approved=any(line.strip() == _APPROVED for line in today.text.splitlines()[:8]),
            blocks=blocks,
            current=current_block(blocks, now),
            upcoming=next_block(blocks, now),
            resting=in_protected(blocks, now),
            checkins=_count_entries(today.text),
            adjustments=parse_adjustments(today.text, today.day),
            zone=today.zone,
            readable=readable,
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
        blocks, _ = self._blocks(today)
        decision = decide(
            state,
            now=self._clock.now(),
            zone=today.zone,
            blocks=blocks,
            task_id=task_id,
            note=note,
        )
        path = self._tools.anexar_checkin(actor, today.path, decision.record)
        if decision.adjustment is not None:
            self._tools.anexar_checkin(actor, today.path, adjustment_entry(decision.adjustment))
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
        )

    def _message(
        self, decision: CoachDecision, actor: ActorContext, use_ai: bool
    ) -> tuple[str, MicroStep | None, bool]:
        if not use_ai or self._writer is None:
            return decision.message, decision.micro_step, False
        try:
            draft = self._writer.write(decision, correlation_id=actor.correlation_id)
        except LlmError:
            # El fallo ya quedó en el registro de uso; el check-in no depende del modelo.
            return decision.message, decision.micro_step, True
        # El modelo no puede añadir un micro-paso que las reglas no pidieron.
        step = (draft.micro_step or decision.micro_step) if decision.micro_step else None
        return draft.message, step, False

    # --- Cierre -----------------------------------------------------------------------------

    def record_missed(self, day: date, blocks: Sequence[Block]) -> tuple[Adjustment, ...]:
        """Propone al Jefe reubicar los bloques que el propietario dio por no realizados."""
        actor = self.new_actor()
        today = self._today(actor, day)
        now = self._clock.now().astimezone(today.zone)
        at = now.time().replace(second=0, microsecond=0)
        recorded = []
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
            self._tools.anexar_checkin(actor, today.path, adjustment_entry(adjustment))
            recorded.append(adjustment)
        return tuple(recorded)
