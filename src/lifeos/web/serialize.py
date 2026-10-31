"""Resultados de los casos de uso como JSON para el frontend: solo presentan, no deciden.

Todo texto que viene de la bóveda, de la configuración o del modelo pasa por `printable`. El
frontend lo muestra como texto (React escapa); nunca se interpreta como HTML.
"""

from collections.abc import Iterable
from typing import Any
from zoneinfo import ZoneInfo

from lifeos.application.agents_overview import AgentOverview, AgentsOverview, GrantView
from lifeos.application.closing import ClosingPreview, ClosingResult
from lifeos.application.coach import Briefing, CheckInOutcome
from lifeos.application.history import ChangeHistory
from lifeos.application.jefe import PlanOutcome, WeekView
from lifeos.application.tasks import LocatedTask
from lifeos.application.usage_report import UsageRow, UsageSummary
from lifeos.domain.adjustments import Adjustment, adjustment_entry
from lifeos.domain.closing import TaskChange
from lifeos.domain.coach import block_span
from lifeos.domain.load import DayLoad
from lifeos.domain.plan import Block, BlockKind, PlanProposal
from lifeos.domain.text import printable

Json = dict[str, Any]


def _text(value: object) -> str:
    return printable(str(value))


def _opt(value: object | None) -> str | None:
    return None if value is None else _text(value)


def block(item: Block, zone: ZoneInfo) -> Json:
    local = item.interval.start.astimezone(zone)
    return {
        "kind": str(item.kind),
        "title": _text(item.title),
        "task_id": _opt(item.task_id),
        "day": local.date().isoformat(),
        "span": block_span(item, zone).render(),
        "start": item.interval.start.isoformat(),
        "end": item.interval.end.isoformat(),
        "minutes": item.interval.minutes,
    }


def _blocks(items: Iterable[Block], zone: ZoneInfo) -> list[Json]:
    ordered = sorted(items, key=lambda b: b.interval.start.timestamp())
    return [block(item, zone) for item in ordered]


def adjustment(item: Adjustment) -> Json:
    return {
        "day": item.day.isoformat(),
        "at": item.at.strftime("%H:%M"),
        "kind": str(item.kind),
        "text": _text(adjustment_entry(item).text),
    }


def task(item: LocatedTask) -> Json:
    data = item.task
    return {
        "id": _opt(data.id),
        "title": _text(data.title),
        "status": str(data.status),
        "area": _opt(data.area),
        "effort": data.effort.render() if data.effort else None,
        "progress": data.progress.render() if data.progress else None,
        "priority": str(data.priority),
        "due": data.due.isoformat() if data.due else None,
        "scheduled": data.scheduled.isoformat() if data.scheduled else None,
        "path": _text(item.path),
        "archived": item.archived,
    }


def tasks(items: Iterable[LocatedTask]) -> list[Json]:
    return [task(item) for item in items]


def briefing(day: Briefing) -> Json:
    zone = day.zone

    def one(item: Block | None) -> Json | None:
        return block(item, zone) if item is not None else None

    return {
        "day": day.day.isoformat(),
        "zone": zone.key,
        "approved": day.approved,
        "readable": day.readable,
        "current": one(day.current),
        "upcoming": one(day.upcoming),
        "resting": one(day.resting),
        "blocks": _blocks(day.blocks, zone),
        "work": _blocks(day.work, zone),
        "protected": _blocks((b for b in day.blocks if b.kind is BlockKind.PROTEGIDO), zone),
        "checkins": day.checkins,
        "adjustments": [adjustment(a) for a in day.adjustments],
        "rejected_adjustments": day.rejected_adjustments,
    }


def checkin(result: CheckInOutcome) -> Json:
    step = result.micro_step
    return {
        "kind": str(result.kind),
        "message": _text(result.message),
        "micro_step": {"action": _text(step.action), "minutes": step.minutes} if step else None,
        "adjustment": adjustment(result.adjustment) if result.adjustment else None,
        "path": _text(result.path),
        "capture": _opt(result.capture),
        "ai_fallback": result.ai_fallback,
        "readable": result.readable,
    }


def load(item: DayLoad) -> Json:
    return {
        "day": item.day.isoformat(),
        "budget_minutes": item.budget_minutes,
        "planned_minutes": item.planned_minutes,
        "fixed_minutes": item.fixed_minutes,
        "protected_minutes": item.protected_minutes,
        "free_minutes": item.free_minutes,
        "over_budget": item.over_budget,
    }


def week(view: WeekView) -> Json:
    return {
        "week": view.week.render(),
        "zone": view.zone.key,
        "path": _opt(view.path),
        "status": _opt(view.status),
        "origin": _opt(view.origin),
        "revision": view.revision,
        "versions": view.versions,
        "generated": view.generated.isoformat() if view.generated else None,
        "justification": _text(view.justification),
        "days": [day.isoformat() for day in view.week.days()],
        "blocks": _blocks(view.blocks, view.zone),
        "load": [load(item) for item in view.load],
    }


def _proposal(proposal: PlanProposal, zone: ZoneInfo) -> Json:
    return {
        "week": proposal.week.render(),
        "origin": str(proposal.origin),
        "work": _blocks(proposal.work_blocks(), zone),
        "unplanned": [
            {
                "task_id": _opt(u.task_id),
                "title": _text(u.title),
                "minutes": u.minutes,
                "reason": _text(u.reason),
                "suggestion": _text(u.suggestion),
            }
            for u in proposal.unplanned
        ],
        "alerts": [_text(alert) for alert in proposal.alerts],
        "justification": _text(proposal.justification),
    }


def plan(result: PlanOutcome) -> Json:
    return {
        "accepted": result.accepted,
        "status": _opt(result.status),
        "path": _opt(result.path),
        "proposal": _proposal(result.proposal, result.zone),
        "conflicts": [
            {
                "code": str(c.code),
                "message": _text(c.message),
                "day": c.day.isoformat() if c.day else None,
            }
            for c in result.conflicts
        ],
    }


def _change(change: TaskChange) -> Json:
    def render(value: object | None) -> str | None:
        render_method = getattr(value, "render", None)
        return render_method() if render_method is not None else None

    return {
        "task_id": _text(change.task_id),
        "title": _text(change.title),
        "kind": str(change.kind),
        "progress_before": render(change.progress_before),
        "progress_after": render(change.progress_after),
        "remaining_before": render(change.remaining_before),
        "remaining_after": render(change.remaining_after),
    }


def closing_preview(preview: ClosingPreview) -> Json:
    return {
        "day": preview.day.isoformat(),
        "path": _text(preview.path),
        "changes": [_change(c) for c in preview.changes],
        "unanswered": [_text(t) for t in preview.unanswered],
        "missed": _blocks(preview.missed, preview.zone),
        "closed": preview.closed,
    }


def closing_result(result: ClosingResult) -> Json:
    return {
        **closing_preview(result.preview),
        "written": [_text(path) for path in result.written],
        "adjustments": [adjustment(a) for a in result.adjustments],
    }


def history(changes: ChangeHistory) -> Json:
    return {
        "entries": [
            {
                "ts": e.ts.isoformat(),
                "actor": e.actor,
                "action": e.action,
                "target": e.target,
                "result": str(e.result),
                "reason": e.reason,
                "completed": e.completed,
            }
            for e in changes.entries
        ],
        "malformed": changes.malformed,
        "truncated": changes.truncated,
    }


def usage_row(row: UsageRow) -> Json:
    return {
        "agent": _text(row.agent),
        "ritual": _text(row.ritual),
        "calls": row.calls,
        "attempts": row.attempts,
        "errors": row.errors,
        "tokens_prompt": row.tokens_prompt,
        "tokens_completion": row.tokens_completion,
        "tokens_unknown": row.tokens_unknown,
        "cost_estimated_usd": str(row.cost_estimated),
        "cost_unknown": row.cost_unknown,
    }


def usage(summary: UsageSummary, month: str) -> Json:
    return {
        "month": month,
        "rows": [usage_row(row) for row in summary.rows],
        "total": usage_row(summary.total),
        "malformed": summary.malformed,
    }


def _grants(grants: Iterable[GrantView]) -> list[Json]:
    return [{"root": _text(g.root), "mode": g.mode, "section": _opt(g.section)} for g in grants]


def _agent(agent: AgentOverview) -> Json:
    return {
        "id": _text(agent.id),
        "name": _text(agent.name),
        "version": agent.version,
        "purpose": _text(agent.purpose),
        "status": agent.status,
        "profile": _text(agent.profile),
        "provider": _text(agent.provider),
        "model": _text(agent.model),
        "contract_in": _text(agent.contract_in),
        "contract_out": _text(agent.contract_out),
        "reads": [_text(r) for r in agent.reads],
        "writes": _grants(agent.writes),
        "plan_writes": _grants(agent.plan_writes),
        "tools": [_text(t) for t in agent.tools],
        "metrics": [_text(m) for m in agent.metrics],
        "usage": usage_row(agent.usage) if agent.usage else None,
    }


def agents(overview: AgentsOverview, month: str) -> Json:
    return {
        "month": month,
        "agents": [_agent(a) for a in overview.agents],
        "plan_owner": _text(overview.plan_owner),
        "forbidden_write": [_text(r) for r in overview.forbidden_write],
        "restricted_read": [_text(r) for r in overview.restricted_read],
        "usage_malformed": overview.usage_malformed,
    }
