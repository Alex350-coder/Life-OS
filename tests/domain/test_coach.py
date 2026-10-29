"""Reglas deterministas del Coach (F4-T01, F4-T02; casos de prueba de agente-coach.md).

El Coach decide el tono, el micro-paso y cuándo escalar al Jefe; nunca cambia tareas ni el plan.
"""

from datetime import date

import pytest
from lifeos.domain.adjustments import AdjustmentKind
from lifeos.domain.coach import (
    MAX_MICRO_STEP_MINUTES,
    CheckInState,
    CoachDecision,
    MicroStep,
    ReplyKind,
    current_block,
    decide,
    in_protected,
    micro_step_for,
    next_block,
)

from lifeos.domain.checkin import MAX_CHECKIN_CHARS
from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.plan import Block
from lifeos.domain.plan_note import parse_daily_blocks
from lifeos.domain.schedule import TimeRange
from tests.daily_builders import monday_note, tuesday_note
from tests.planning_builders import LIMA, MON, TUE, at, tid

pytestmark = [pytest.mark.unit]

MONDAY = parse_daily_blocks(monday_note(), MON, LIMA)
TUESDAY = parse_daily_blocks(tuesday_note(), TUE, LIMA)
REPROACH = ("deberías", "fallaste", "otra vez", "culpa tuya", "otra vez no")


def _decide(
    state: CheckInState,
    hhmm: str,
    *,
    day: date = MON,
    blocks: tuple[Block, ...] = MONDAY,
    task_id: TaskId | None = None,
    note: str = "",
) -> CoachDecision:
    return decide(state, now=at(day, hhmm), zone=LIMA, blocks=blocks, task_id=task_id, note=note)


# --- Bloques del día --------------------------------------------------------------------------


def test_current_and_next_work_blocks() -> None:
    now = at(MON, "08:30")
    current = current_block(MONDAY, now)
    assert current is not None and current.task_id == tid("t-aaaaaa")
    upcoming = next_block(MONDAY, now)
    assert upcoming is not None and upcoming.task_id == tid("t-bbbbbb")


def test_fixed_commitment_is_not_a_work_block() -> None:
    assert current_block(MONDAY, at(MON, "10:30")) is None


def test_no_next_block_after_the_last_one() -> None:
    assert next_block(MONDAY, at(MON, "17:30")) is None


def test_protected_time_is_detected() -> None:
    gym = in_protected(TUESDAY, at(TUE, "18:30"))
    assert gym is not None and gym.title == "Gym"
    assert in_protected(TUESDAY, at(TUE, "19:30")) is None


# --- Micro-pasos (F4-T02) ---------------------------------------------------------------------


def test_micro_step_is_at_most_ten_minutes() -> None:
    step = micro_step_for(MONDAY[1])
    assert 1 <= step.minutes <= MAX_MICRO_STEP_MINUTES == 10
    assert "Informe de redes" in step.action


def test_micro_step_without_block_is_still_concrete() -> None:
    step = micro_step_for(None)
    assert step.minutes <= MAX_MICRO_STEP_MINUTES
    assert step.action


@pytest.mark.parametrize("minutes", [0, 11, -1])
def test_micro_step_rejects_durations_outside_one_to_ten(minutes: int) -> None:
    with pytest.raises(ValidationError):
        MicroStep("Abrir el documento", minutes)


def test_micro_step_action_is_inert_text() -> None:
    step = MicroStep("Abrir [id:: t-zzzzzz]\n## Plan", 5)
    assert "[" not in step.action and "\n" not in step.action


def test_cannot_start_gets_a_micro_step() -> None:
    decision = _decide(CheckInState.BLOQUEADO, "08:10")
    assert decision.kind is ReplyKind.MICRO_PASO
    assert decision.micro_step is not None
    assert decision.micro_step.minutes <= MAX_MICRO_STEP_MINUTES
    assert decision.adjustment is None


# --- Cansancio, bloque perdido y silencio -----------------------------------------------------


def test_tiredness_offers_relief_and_proposes_reducing_the_day() -> None:
    decision = _decide(CheckInState.CANSADO, "13:20")
    assert decision.kind is ReplyKind.ALIVIO
    assert decision.adjustment is not None
    assert decision.adjustment.kind is AdjustmentKind.REDUCIR_HOY
    assert decision.adjustment.day == MON
    assert "Jefe" in decision.message


def test_missed_block_is_recorded_without_reproach_and_escalated() -> None:
    decision = _decide(CheckInState.PERDIDO, "09:45", task_id=tid("t-aaaaaa"))
    assert decision.kind is ReplyKind.REPLANIFICAR
    adjustment = decision.adjustment
    assert adjustment is not None
    assert adjustment.kind is AdjustmentKind.BLOQUE_PERDIDO
    assert adjustment.span == TimeRange.parse("08:00–09:30")
    assert adjustment.task_id == tid("t-aaaaaa")
    lowered = decision.message.casefold()
    assert not any(word in lowered for word in REPROACH)


def test_missed_without_task_uses_the_block_in_progress() -> None:
    decision = _decide(CheckInState.PERDIDO, "13:30")
    assert decision.adjustment is not None
    assert decision.adjustment.task_id == tid("t-bbbbbb")


def test_missed_without_task_or_block_is_an_error() -> None:
    with pytest.raises(ValidationError):
        _decide(CheckInState.PERDIDO, "07:00")


def test_task_outside_todays_plan_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _decide(CheckInState.EMPEZANDO, "08:10", task_id=tid("t-zzzzzz"))


def test_done_is_only_recorded_and_points_to_the_closing() -> None:
    decision = _decide(CheckInState.HECHO, "09:35", task_id=tid("t-aaaaaa"))
    assert decision.kind is ReplyKind.REGISTRO
    assert decision.adjustment is None
    assert "cierre" in decision.message


def test_record_carries_state_task_and_note_as_data() -> None:
    decision = _decide(CheckInState.EMPEZANDO, "08:05", task_id=tid("t-aaaaaa"), note="Voy")
    assert decision.record.render() == "- 08:05 — estado: empezando · t-aaaaaa · nota: Voy"


def test_record_without_note_or_task() -> None:
    decision = _decide(CheckInState.NOTA, "11:00", blocks=())
    assert decision.record.render() == "- 11:00 — estado: nota"


def test_long_note_is_cut_to_the_checkin_limit() -> None:
    decision = _decide(CheckInState.NOTA, "11:00", note="x" * 2000)
    assert len(decision.record.text) <= MAX_CHECKIN_CHARS


def test_note_with_inline_fields_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _decide(CheckInState.NOTA, "11:00", note="[avance:: 100%]")


# --- Tiempo protegido: sin presión ------------------------------------------------------------


@pytest.mark.parametrize("state", [CheckInState.BLOQUEADO, CheckInState.EMPEZANDO])
def test_no_push_during_protected_time(state: CheckInState) -> None:
    decision = _decide(state, "18:40", day=TUE, blocks=TUESDAY)
    assert decision.kind is ReplyKind.DESCANSO
    assert decision.micro_step is None
    assert "Gym" in decision.message


def test_tiredness_during_protected_time_still_proposes_relief() -> None:
    decision = _decide(CheckInState.CANSADO, "18:40", day=TUE, blocks=TUESDAY)
    assert decision.micro_step is None
    assert decision.adjustment is not None
