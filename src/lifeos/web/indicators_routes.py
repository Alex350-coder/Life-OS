"""`GET /api/indicadores` (F9-T03): la misma lectura que `lifeos-indicadores`, en JSON.

Solo lectura y sin reglas (ADR-0006). Sesión, Host y Origin los aplica el middleware (SEC-11).
Los valores desconocidos viajan como `null`, nunca como 0.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from lifeos.application.indicator_report import IndicatorReport, measure_indicators
from lifeos.application.indicators import (
    DEFAULT_WINDOW_DAYS,
    MAX_WINDOW_DAYS,
    NotificationLoad,
    RitualCost,
)
from lifeos.domain.text import printable
from lifeos.web.errors import ok
from lifeos.web.runtime import WebRuntime, vault

router = APIRouter(prefix="/api")
type Json = dict[str, object]
# Leer la bóveda y hasta 4 meses de logs es caro: cada ventana se reutiliza unos segundos para
# que una vista que se monta varias veces no repita el trabajo (revisión de seguridad, Fase 9).
CACHE_TTL_S = 30.0


@dataclass(slots=True)
class ReportCache:
    clock: Callable[[], float] = time.monotonic
    entries: dict[int, tuple[float, Json]] = field(default_factory=dict)

    def get(self, days: int) -> Json | None:
        hit = self.entries.get(days)
        if hit is None or self.clock() - hit[0] >= CACHE_TTL_S:
            return None
        return hit[1]

    def put(self, days: int, data: Json) -> None:
        self.entries[days] = (self.clock(), data)


def _cache(request: Request) -> ReportCache:
    cache = getattr(request.app.state, "indicators_cache", None)
    if not isinstance(cache, ReportCache):
        cache = ReportCache()
        request.app.state.indicators_cache = cache
    return cache


def _cost(row: RitualCost) -> Json:
    return {
        "agent": printable(row.agent),
        "ritual": printable(row.ritual),
        "calls": row.calls,
        "errors": row.errors,
        "latency_median_ms": row.latency_median_ms,
        "latency_p95_ms": row.latency_p95_ms,
        "cost_estimated_usd": str(row.cost_estimated),
        "cost_unknown": row.cost_unknown,
    }


def _notifications(load: NotificationLoad) -> Json:
    return {
        "sent": load.sent,
        "failed": load.failed,
        "skipped": load.skipped,
        "days_with_notifications": load.days_with_notifications,
        "max_per_day": load.max_per_day,
        "per_active_day": load.per_active_day,
        "by_kind": [{"kind": printable(kind), "count": count} for kind, count in load.by_kind],
    }


def serialize(report: IndicatorReport) -> Json:
    d, a, u, p = report.deliveries, report.adherence, report.usage, report.acceptance
    return {
        "start": report.window.start.isoformat(),
        "end": report.window.end.isoformat(),
        "deliveries": {
            "due": d.due,
            "completed": d.completed,
            "forgotten": d.forgotten,
            "percent": d.percent,
        },
        "streak": report.streak,
        "focus_max_minutes": report.focus_max_minutes,
        "adherence": {"closed_days": a.closed_days, "answered": a.answered, "percent": a.percent},
        "usage": {"active_days": u.active_days, "days_per_week": u.days_per_week},
        "acceptance": {
            "proposed": p.proposed,
            "approved": p.approved,
            "minor_changes": p.minor_changes,
            "percent": p.percent,
        },
        "costs": [_cost(row) for row in report.costs],
        "notifications": _notifications(report.notifications),
        "not_measured": [{"name": n, "reason": r} for n, r in report.not_measured],
        "quality": {
            "malformed_log_lines": report.quality.malformed_log_lines,
            "unreadable_notes": report.quality.unreadable_notes,
            "invalid_task_lines": report.quality.invalid_task_lines,
            "unreadable_closings": report.quality.unreadable_closings,
        },
    }


@router.get("/indicadores")
def indicadores(
    request: Request,
    dias: Annotated[int, Query(ge=1, le=MAX_WINDOW_DAYS)] = DEFAULT_WINDOW_DAYS,
) -> JSONResponse:
    cache = _cache(request)
    cached = cache.get(dias)
    if cached is not None:
        return ok(cached)
    web: WebRuntime = request.app.state.web
    runtime = web.base
    report = measure_indicators(
        vault(runtime), runtime.usage_reader, runtime.audit_reader, runtime.clock.now(), days=dias
    )
    data = serialize(report)
    cache.put(dias, data)
    return ok(data)
