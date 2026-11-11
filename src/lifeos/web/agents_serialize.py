"""Registro de agentes y propuestas como JSON (Fase 8). Solo presentan; todo texto pasa por
`printable` y el frontend lo muestra como texto."""

from typing import Any

from lifeos.application.agent_registry import RegistryView
from lifeos.application.permissions import EffectivePermissions
from lifeos.application.proposal_review import AcceptResult, ReviewItem
from lifeos.application.specialists import SpecialistResult
from lifeos.domain.area_proposals import ProposalItem
from lifeos.domain.text import printable

Json = dict[str, Any]
HASH_CHARS = 12
MAX_HISTORY = 20


def text(value: object) -> str:
    return printable(str(value))


def _opt(value: object | None) -> str | None:
    return None if value is None else text(value)


def effective(perms: EffectivePermissions) -> Json:
    return {
        "reads": [f"{root}/" for root in perms.reads],
        "writes": [
            {"root": f"{g.root}/", "mode": str(g.mode), "section": _opt(g.section)}
            for g in perms.writes
        ],
        "tools": sorted(perms.tools),
    }


def registry_view(view: RegistryView) -> Json:
    entry, config = view.entry, view.config
    return {
        "id": text(entry.agent_id),
        "name": text(config.name) if config else text(entry.agent_id),
        "purpose": text(config.purpose) if config else "",
        "area": _opt(config.area) if config else None,
        "profile": text(config.profile) if config else None,
        "state": str(entry.state),
        "current": entry.current,
        "versions": [
            {
                "number": v.number,
                "config_version": v.config_version,
                "sha256": v.sha256[:HASH_CHARS],
                "imported_at": v.imported_at.isoformat(),
            }
            for v in entry.versions
        ],
        "history": [
            {
                "at": t.at.isoformat(),
                "action": str(t.action),
                "state": str(t.to_state),
                "version": t.version,
            }
            for t in entry.history[-MAX_HISTORY:]
        ],
        "effective": effective(view.effective) if view.effective else None,
        "problems": [text(p) for p in view.problems],
    }


def item(value: ProposalItem) -> Json:
    return {
        "key": value.key,
        "kind": str(value.kind),
        "priority": str(value.priority),
        "reason": text(value.reason),
        "task_id": _opt(value.task_id),
        "title": _opt(value.title),
        "effort": value.effort.render() if value.effort else None,
        "due": value.due.isoformat() if value.due else None,
        "status": str(value.status),
    }


def run_result(result: SpecialistResult) -> Json:
    proposal = result.proposal
    return {
        "agent_id": text(proposal.agent_id),
        "week": str(proposal.week),
        "source": proposal.source,
        "dry_run": result.dry_run,
        "recorded_test": result.recorded_test,
        "path": _opt(result.path),
        "items": [item(i) for i in proposal.items],
        "alerts": [text(a) for a in result.alerts],
    }


def review_item(review: ReviewItem) -> Json:
    return {
        **item(review.item),
        "ref": str(review.ref),
        "area": _opt(review.area),
        "task_title": _opt(review.task_title),
        "current_priority": _opt(review.current_priority),
    }


def accept_result(result: AcceptResult) -> Json:
    return {"ref": str(result.ref), "task_id": _opt(result.task_id)}
