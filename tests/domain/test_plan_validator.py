"""Validador determinista de planes (F3-T02, SEC-09: un plan inválido se rechaza)."""

import pytest

from lifeos.domain.errors import ValidationError
from lifeos.domain.plan import (
    Block,
    BlockKind,
    ConflictCode,
    PlanProposal,
    fixed_and_protected_blocks,
)
from lifeos.domain.plan_validator import validate_plan
from tests.planning_builders import (
    FRI,
    MON,
    NOW,
    SAT,
    THU,
    TUE,
    WED,
    WEEK,
    at,
    context,
    span,
    task,
    tid,
    work,
)

pytestmark = pytest.mark.unit

A = "- [ ] Informe [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 4h]"
B = "- [ ] Lectura [id:: t-bbbbbb] [area:: universidad] [esfuerzo:: 1h] [depende:: t-aaaaaa]"
C = "- [ ] Corto [id:: t-cccccc] [area:: cotidiano] [esfuerzo:: 20m]"
D = "- [ ] Ensayo [id:: t-dddddd] [area:: hobbies] [esfuerzo:: 90m] [divisible:: no]"
DONE = "- [x] Hecha [id:: t-eeeeee] [area:: carrera] [esfuerzo:: 1h]"
BLOCKED = "- [ ] Bloqueada [id:: t-ffffff] [area:: carrera] [esfuerzo:: 1h] [estado:: bloqueada]"
DUE_TUE = "- [ ] Entrega [id:: t-gggggg] [area:: universidad] [esfuerzo:: 1h] 📅 2026-11-03"


def ctx():  # type: ignore[no-untyped-def]
    return context(task(A), task(B), task(C), task(D), task(DONE), task(BLOCKED), task(DUE_TUE))


def plan(*work_blocks: Block, base: tuple[Block, ...] | None = None) -> PlanProposal:
    fixed = fixed_and_protected_blocks(ctx()) if base is None else base
    return PlanProposal(week=WEEK, blocks=(*fixed, *work_blocks))


def codes(proposal: PlanProposal) -> set[ConflictCode]:
    return {conflict.code for conflict in validate_plan(proposal, ctx())}


def test_valid_plan_has_no_conflicts() -> None:
    proposal = plan(
        work(MON, "08:00–10:00", "t-aaaaaa"),
        work(TUE, "08:00–10:00", "t-aaaaaa"),
        work(WED, "08:00–09:00", "t-bbbbbb"),
        work(WED, "12:00–12:20", "t-cccccc"),
        work(THU, "08:00–09:30", "t-dddddd"),
        work(MON, "12:00–13:00", "t-gggggg"),
    )
    assert validate_plan(proposal, ctx()) == ()


def test_overlapping_blocks_are_rejected() -> None:
    proposal = plan(work(MON, "08:00–09:30", "t-aaaaaa"), work(MON, "09:00–09:20", "t-cccccc"))
    assert ConflictCode.SOLAPAMIENTO in codes(proposal)


def test_touching_blocks_do_not_overlap() -> None:
    proposal = plan(work(MON, "08:00–09:00", "t-aaaaaa"), work(MON, "09:00–09:20", "t-cccccc"))
    assert codes(proposal) == set()


def test_work_on_protected_time_is_rejected() -> None:
    assert ConflictCode.TIEMPO_PROTEGIDO in codes(plan(work(TUE, "18:00–19:00", "t-aaaaaa")))


def test_work_on_a_fixed_commitment_is_rejected() -> None:
    assert ConflictCode.COMPROMISO_FIJO in codes(plan(work(MON, "10:30–11:30", "t-aaaaaa")))


def test_removed_fixed_commitment_is_rejected() -> None:
    base = tuple(b for b in fixed_and_protected_blocks(ctx()) if b.kind is not BlockKind.FIJO)
    assert ConflictCode.FIJO_ALTERADO in codes(plan(base=base))


def test_moved_fixed_commitment_is_rejected() -> None:
    base = tuple(
        Block(b.kind, b.title, span(MON, "13:00–15:00"))
        if b.kind is BlockKind.FIJO and b.interval.start.date() == MON
        else b
        for b in fixed_and_protected_blocks(ctx())
    )
    assert ConflictCode.FIJO_ALTERADO in codes(plan(base=base))


def test_invented_fixed_commitment_is_rejected() -> None:
    extra = Block(BlockKind.FIJO, "Inventado", span(SAT, "09:00–10:00"))
    assert ConflictCode.FIJO_ALTERADO in codes(plan(extra))


def test_removed_protected_time_is_rejected() -> None:
    base = tuple(b for b in fixed_and_protected_blocks(ctx()) if b.kind is not BlockKind.PROTEGIDO)
    assert ConflictCode.PROTEGIDO_ELIMINADO in codes(plan(base=base))


def test_daily_overload_is_rejected() -> None:
    proposal = plan(
        work(MON, "08:00–10:00", "t-aaaaaa"),
        work(MON, "12:00–14:00", "t-aaaaaa"),
        work(MON, "14:00–15:00", "t-bbbbbb"),
    )
    assert ConflictCode.EXCESO_DE_CARGA in codes(proposal)


@pytest.mark.parametrize(
    "block",
    [work(MON, "20:00–21:00", "t-aaaaaa"), work(FRI, "09:00–10:00", "t-aaaaaa")],
    ids=["after-hours", "travel-day"],
)
def test_work_outside_availability_is_rejected(block: Block) -> None:
    assert ConflictCode.FUERA_DE_DISPONIBILIDAD in codes(plan(block))


def test_past_work_is_rejected() -> None:
    late = context(task(A), now=at(MON, "12:30"))
    proposal = PlanProposal(
        week=WEEK, blocks=(*fixed_and_protected_blocks(late), work(MON, "08:00–09:00", "t-aaaaaa"))
    )
    found = {conflict.code for conflict in validate_plan(proposal, late)}
    assert ConflictCode.FECHA_PASADA in found


def test_block_outside_the_week_is_rejected() -> None:
    assert ConflictCode.FUERA_DE_SEMANA in codes(
        plan(work(MON.replace(day=9), "08:00–09:00", "t-aaaaaa"))
    )


def test_unknown_task_is_rejected() -> None:
    assert ConflictCode.TAREA_DESCONOCIDA in codes(plan(work(MON, "08:00–09:00", "t-zzzzzz")))


@pytest.mark.parametrize("task_id", ["t-eeeeee", "t-ffffff"], ids=["completed", "blocked"])
def test_non_plannable_task_is_rejected(task_id: str) -> None:
    assert ConflictCode.TAREA_NO_PLANIFICABLE in codes(plan(work(MON, "08:00–09:00", task_id)))


def test_more_time_than_remaining_effort_is_rejected() -> None:
    proposal = plan(work(MON, "08:00–08:40", "t-cccccc"))
    assert ConflictCode.EXCEDE_ESFUERZO in codes(proposal)


def test_block_shorter_than_minimum_is_rejected_unless_it_is_the_whole_task() -> None:
    assert ConflictCode.BLOQUE_CORTO in codes(plan(work(MON, "08:00–08:15", "t-aaaaaa")))
    assert codes(plan(work(MON, "08:00–08:20", "t-cccccc"))) == set()


def test_block_longer_than_maximum_is_rejected() -> None:
    assert ConflictCode.BLOQUE_LARGO in codes(plan(work(TUE, "08:00–10:30", "t-aaaaaa")))


def test_indivisible_task_must_be_one_complete_block() -> None:
    split = plan(work(MON, "08:00–08:45", "t-dddddd"), work(TUE, "08:00–08:45", "t-dddddd"))
    partial = plan(work(MON, "08:00–09:00", "t-dddddd"))
    assert ConflictCode.NO_DIVISIBLE_PARTIDA in codes(split)
    assert ConflictCode.NO_DIVISIBLE_PARTIDA in codes(partial)


def test_dependent_task_before_its_dependency_is_rejected() -> None:
    proposal = plan(
        work(MON, "08:00–09:00", "t-bbbbbb"),
        work(MON, "12:00–14:00", "t-aaaaaa"),
        work(TUE, "08:00–10:00", "t-aaaaaa"),
    )
    assert ConflictCode.DEPENDENCIA in codes(proposal)


def test_dependency_that_is_not_fully_planned_is_rejected() -> None:
    proposal = plan(work(MON, "08:00–10:00", "t-aaaaaa"), work(TUE, "08:00–09:00", "t-bbbbbb"))
    assert ConflictCode.DEPENDENCIA in codes(proposal)


def test_dependency_already_completed_is_satisfied() -> None:
    dependent = (
        "- [ ] Siguiente [id:: t-hhhhhh] [area:: carrera] [esfuerzo:: 1h] [depende:: t-eeeeee]"
    )
    local = context(task(DONE), task(dependent))
    proposal = PlanProposal(
        week=WEEK,
        blocks=(*fixed_and_protected_blocks(local), work(MON, "08:00–09:00", "t-hhhhhh")),
    )
    assert validate_plan(proposal, local) == ()


def test_work_after_the_due_date_is_rejected() -> None:
    assert ConflictCode.DESPUES_DE_FECHA_LIMITE in codes(plan(work(WED, "08:00–09:00", "t-gggggg")))


def test_overdue_task_may_still_be_planned() -> None:
    overdue = "- [ ] Atrasada [id:: t-jjjjjj] [area:: carrera] [esfuerzo:: 1h] 📅 2026-10-20"
    local = context(task(overdue))
    proposal = PlanProposal(
        week=WEEK,
        blocks=(*fixed_and_protected_blocks(local), work(MON, "08:00–09:00", "t-jjjjjj")),
    )
    assert validate_plan(proposal, local) == ()


def test_conflicts_carry_a_readable_reason_and_the_day() -> None:
    conflicts = validate_plan(plan(work(TUE, "18:00–19:00", "t-aaaaaa")), ctx())
    protected = [c for c in conflicts if c.code is ConflictCode.TIEMPO_PROTEGIDO]
    assert protected[0].day == TUE
    assert "Gym" in protected[0].message


def test_validation_is_deterministic() -> None:
    proposal = plan(work(MON, "08:00–09:30", "t-aaaaaa"), work(MON, "09:00–09:20", "t-cccccc"))
    assert validate_plan(proposal, ctx()) == validate_plan(proposal, ctx())


def test_work_block_requires_a_task_and_schedule_blocks_must_not_have_one() -> None:
    with pytest.raises(ValidationError):
        Block(BlockKind.TRABAJO, "x", span(MON, "08:00–09:00"))
    with pytest.raises(ValidationError):
        Block(BlockKind.FIJO, "x", span(MON, "08:00–09:00"), tid("t-aaaaaa"))


def test_context_uses_the_given_now() -> None:
    assert ctx().not_before == NOW
