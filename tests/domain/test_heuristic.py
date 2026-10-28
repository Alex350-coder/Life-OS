"""Heurística determinista y tareas que no caben (F3-T01, F3-T04).

Toda propuesta de la heurística debe pasar el validador: nunca se presenta un plan inválido.
"""

from collections import defaultdict
from datetime import date

import pytest

from lifeos.domain.plan import (
    Block,
    BlockKind,
    PlanningContext,
    PlanProposal,
    fixed_and_protected_blocks,
)
from lifeos.domain.heuristic import propose
from lifeos.domain.plan_validator import validate_plan
from tests.planning_builders import FRI, MON, SAT, SUN, TUE, WEEK, at, context, task, tid

pytestmark = pytest.mark.unit


def line(
    task_id: str,
    effort: str,
    *,
    title: str = "Tarea",
    extra: str = "",
    mark: str = " ",
) -> str:
    return f"- [{mark}] {title} [id:: {task_id}] [area:: universidad] [esfuerzo:: {effort}] {extra}"


def run(*lines: str, **kwargs: object) -> tuple[PlanningContext, PlanProposal]:
    ctx = context(*(task(text) for text in lines), **kwargs)  # type: ignore[arg-type]
    proposal = propose(ctx)
    assert validate_plan(proposal, ctx) == (), "la heurística propuso un plan inválido"
    return ctx, proposal


def work_of(proposal: PlanProposal, task_id: str) -> list[Block]:
    return [b for b in proposal.blocks if b.task_id == tid(task_id)]


def minutes(blocks: list[Block]) -> int:
    return sum(b.interval.minutes for b in blocks)


def day_of(block: Block) -> date:
    return block.interval.start.date()


def test_simple_task_is_planned_on_the_first_free_slot() -> None:
    _, proposal = run(line("t-aaaaaa", "1h"))
    blocks = work_of(proposal, "t-aaaaaa")
    assert [(b.interval.start, b.interval.end) for b in blocks] == [
        (at(MON, "08:00"), at(MON, "09:00"))
    ]
    assert blocks[0].title == "Tarea"
    assert proposal.unplanned == ()


def test_fixed_commitments_and_protected_time_are_kept_unchanged() -> None:
    ctx, proposal = run(line("t-aaaaaa", "12h"))
    schedule_blocks = [b for b in proposal.blocks if b.kind is not BlockKind.TRABAJO]
    assert schedule_blocks == list(fixed_and_protected_blocks(ctx))


def test_long_divisible_task_is_split_between_minimum_and_maximum() -> None:
    _, proposal = run(line("t-aaaaaa", "5h"))
    blocks = work_of(proposal, "t-aaaaaa")
    assert minutes(blocks) == 300
    assert all(30 <= b.interval.minutes <= 120 for b in blocks)


def test_split_never_leaves_a_remainder_shorter_than_the_minimum() -> None:
    _, proposal = run(line("t-aaaaaa", "130m"))
    assert all(b.interval.minutes >= 30 for b in work_of(proposal, "t-aaaaaa"))
    assert minutes(work_of(proposal, "t-aaaaaa")) == 130


def test_indivisible_task_goes_in_one_block() -> None:
    _, proposal = run(line("t-aaaaaa", "90m", extra="[divisible:: no]"))
    assert [b.interval.minutes for b in work_of(proposal, "t-aaaaaa")] == [90]


def test_indivisible_task_longer_than_the_maximum_block_is_reported() -> None:
    _, proposal = run(line("t-aaaaaa", "3h", extra="[divisible:: no]"))
    assert work_of(proposal, "t-aaaaaa") == []
    assert proposal.unplanned[0].task_id == tid("t-aaaaaa")
    assert "no divisible" in proposal.unplanned[0].reason


def test_urgent_task_goes_first_and_avoids_its_due_day() -> None:
    _, proposal = run(
        line("t-bbbbbb", "2h", title="Sin fecha"),
        line("t-aaaaaa", "1h", title="Urgente", extra="📅 2026-11-03"),
    )
    urgent = work_of(proposal, "t-aaaaaa")
    assert [day_of(b) for b in urgent] == [MON]
    assert urgent[0].interval.start == at(MON, "08:00")


def test_task_due_today_is_planned_today_when_there_is_no_earlier_day() -> None:
    _, proposal = run(line("t-aaaaaa", "1h", extra="📅 2026-11-02"))
    assert [day_of(b) for b in work_of(proposal, "t-aaaaaa")] == [MON]


def test_higher_priority_goes_first_with_the_same_due_date() -> None:
    _, proposal = run(
        line("t-bbbbbb", "1h", extra="[prioridad:: baja] 📅 2026-11-06"),
        line("t-aaaaaa", "1h", extra="[prioridad:: alta] 📅 2026-11-06"),
    )
    first = min(proposal.blocks, key=lambda b: b.interval.start if b.task_id else at(SUN, "23:00"))
    assert first.task_id == tid("t-aaaaaa")


def test_overdue_task_is_planned_as_soon_as_possible_with_an_alert() -> None:
    _, proposal = run(line("t-aaaaaa", "1h", extra="📅 2026-10-20"))
    assert [day_of(b) for b in work_of(proposal, "t-aaaaaa")] == [MON]
    assert any("vencida" in alert for alert in proposal.alerts)


def test_insufficient_capacity_reports_unplanned_and_keeps_rest() -> None:
    ctx, proposal = run(line("t-aaaaaa", "30h"))
    assert minutes(work_of(proposal, "t-aaaaaa")) == sum(d.budget_minutes for d in ctx.days)
    (pending,) = proposal.unplanned
    assert pending.minutes == 30 * 60 - minutes(work_of(proposal, "t-aaaaaa"))
    assert "2026-W46" in pending.suggestion
    _assert_free_time_minimum_respected(ctx, proposal)


def test_task_that_cannot_fit_before_its_due_date_suggests_renegotiating() -> None:
    _, proposal = run(line("t-aaaaaa", "10h", extra="📅 2026-11-03"))
    (pending,) = proposal.unplanned
    assert pending.minutes > 0
    assert "fecha límite" in pending.suggestion


def test_days_without_capacity_get_no_work() -> None:
    _, proposal = run(line("t-aaaaaa", "30h"))
    worked_days = {day_of(b) for b in proposal.blocks if b.kind is BlockKind.TRABAJO}
    assert FRI not in worked_days
    assert SUN not in worked_days


def test_dependency_is_planned_before_the_dependent_task() -> None:
    _, proposal = run(
        line("t-bbbbbb", "1h", extra="[depende:: t-aaaaaa]"),
        line("t-aaaaaa", "3h"),
    )
    last_a = max(b.interval.end for b in work_of(proposal, "t-aaaaaa"))
    first_b = min(b.interval.start for b in work_of(proposal, "t-bbbbbb"))
    assert last_a <= first_b


def test_dependent_of_a_blocked_or_unknown_task_is_not_planned() -> None:
    _, proposal = run(
        line("t-aaaaaa", "1h", extra="[estado:: bloqueada]"),
        line("t-bbbbbb", "1h", extra="[depende:: t-aaaaaa]"),
        line("t-cccccc", "1h", extra="[depende:: t-zzzzzz]"),
    )
    reasons = {p.task_id: p.reason for p in proposal.unplanned}
    assert "dependencia" in reasons[tid("t-bbbbbb")]
    assert "dependencia" in reasons[tid("t-cccccc")]
    assert tid("t-aaaaaa") not in reasons  # bloqueada: no se planifica ni se reporta


def test_circular_dependencies_are_reported() -> None:
    _, proposal = run(
        line("t-aaaaaa", "1h", extra="[depende:: t-bbbbbb]"),
        line("t-bbbbbb", "1h", extra="[depende:: t-aaaaaa]"),
    )
    assert {p.task_id for p in proposal.unplanned} == {tid("t-aaaaaa"), tid("t-bbbbbb")}
    assert all("circular" in p.reason for p in proposal.unplanned)


def test_completed_dependency_is_satisfied() -> None:
    _, proposal = run(
        line("t-aaaaaa", "1h", mark="x"), line("t-bbbbbb", "1h", extra="[depende:: t-aaaaaa]")
    )
    assert minutes(work_of(proposal, "t-bbbbbb")) == 60


def test_tasks_missing_critical_information_are_reported_as_uncertain() -> None:
    _, proposal = run(
        "- [ ] Sin esfuerzo [id:: t-aaaaaa] [area:: universidad]",
        "- [ ] Sin id [area:: universidad] [esfuerzo:: 1h]",
    )
    reasons = sorted(p.reason for p in proposal.unplanned)
    assert any("esfuerzo" in reason for reason in reasons)
    assert any("id" in reason for reason in reasons)
    assert all(b.kind is not BlockKind.TRABAJO for b in proposal.blocks)


def test_closed_tasks_are_ignored() -> None:
    _, proposal = run(line("t-aaaaaa", "1h", mark="x"), line("t-bbbbbb", "1h", mark="-"))
    assert proposal.unplanned == ()
    assert all(b.kind is not BlockKind.TRABAJO for b in proposal.blocks)


def test_inferred_effort_produces_an_alert() -> None:
    _, proposal = run(line("t-aaaaaa", "1h", extra="[inferido:: true]"))
    assert any("inferid" in alert for alert in proposal.alerts)


def test_planning_from_the_middle_of_the_week_skips_the_past() -> None:
    _, proposal = run(line("t-aaaaaa", "1h"), now=at(TUE, "17:30"))
    (block,) = work_of(proposal, "t-aaaaaa")
    # Al martes le quedan 60 min libres, justo el mínimo libre: presupuesto 0 → miércoles.
    assert block.interval.start == at(TUE.replace(day=4), "08:00")


def test_heuristic_is_deterministic() -> None:
    lines = (line("t-aaaaaa", "5h"), line("t-bbbbbb", "2h", extra="📅 2026-11-05"))
    assert run(*lines)[1] == run(*lines)[1]


def test_justification_summarises_the_result() -> None:
    _, proposal = run(line("t-aaaaaa", "30h"), line("t-bbbbbb", "1h"))
    assert "heurística" in proposal.justification
    assert proposal.week == WEEK


def test_saturday_uses_its_shorter_budget() -> None:
    _, proposal = run(line("t-aaaaaa", "30h"))
    saturday = [b for b in work_of(proposal, "t-aaaaaa") if day_of(b) == SAT]
    assert minutes(saturday) == 180


def _assert_free_time_minimum_respected(ctx: PlanningContext, proposal: PlanProposal) -> None:
    worked: dict[date, int] = defaultdict(int)
    for block in proposal.blocks:
        if block.kind is BlockKind.TRABAJO:
            worked[day_of(block)] += block.interval.minutes
    for day in ctx.days:
        assert worked[day.day] <= day.budget_minutes
        if day.free_minutes:
            assert day.free_minutes - worked[day.day] >= ctx.limits.free_min
