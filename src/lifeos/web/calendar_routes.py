"""Google Calendar en la web, solo lectura (F10-T04, DT-27, ADR-0017).

- `GET /api/calendario`: qué permisos están conectados. Lee los tokens locales y nunca llama
  a Google ni devuelve el token.
- `GET /api/calendario/disponibilidad`: lo mismo que `lifeos-calendario disponibilidad`, es
  decir, los intervalos ocupados de la semana y los bloques del plan que chocan con ellos.

Conectar, publicar y revocar siguen solo en la CLI: necesitan el navegador, una vista previa y
`--confirmar` (RULES G.4 y G.5). Esta web no tiene ninguna ruta que escriba en Google. Sesión,
Host y Origin los aplica el middleware (SEC-11).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from lifeos.application.jefe import ExternalView
from lifeos.bootstrap import GOOGLE_CLIENT_ID_ENV, calendar_access
from lifeos.domain.external import ExternalBusy
from lifeos.domain.text import printable
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.calendar import GrantStatus
from lifeos.web import serialize
from lifeos.web.errors import ok
from lifeos.web.runtime import WebRuntime, jefe

router = APIRouter(prefix="/api/calendario")
type Json = dict[str, object]
# Cada lectura de la disponibilidad es una llamada a Google: una vista que se monta varias
# veces reutiliza la respuesta unos segundos, como los indicadores.
CACHE_TTL_S = 30.0
WEEK_CHARS = 10  # AAAA-Wnn


@dataclass(slots=True)
class AvailabilityCache:
    clock: Callable[[], float] = time.monotonic
    entries: dict[str, tuple[float, Json]] = field(default_factory=dict)

    def get(self, key: str) -> Json | None:
        hit = self.entries.get(key)
        if hit is None or self.clock() - hit[0] >= CACHE_TTL_S:
            return None
        return hit[1]

    def put(self, key: str, data: Json) -> None:
        self.entries[key] = (self.clock(), data)


def _cache(request: Request) -> AvailabilityCache:
    cache = getattr(request.app.state, "calendar_cache", None)
    if not isinstance(cache, AvailabilityCache):
        cache = AvailabilityCache()
        request.app.state.calendar_cache = cache
    return cache


def _grant(status: GrantStatus) -> Json:
    return {
        "grant": status.grant,
        "scope": status.scope,
        "connected": status.connected,
        "obtained": status.obtained.isoformat() if status.obtained is not None else None,
        "problem": printable(status.problem) if status.problem else None,
    }


def _span(zone: ZoneInfo, start: datetime, end: datetime) -> str:
    a, b = start.astimezone(zone), end.astimezone(zone)
    tail = f"{b:%H:%M}" if b.date() == a.date() else f"{b:%Y-%m-%d %H:%M}"
    return f"{a:%H:%M}–{tail}"


def _busy(item: ExternalBusy, zone: ZoneInfo) -> Json:
    start, end = item.interval.start, item.interval.end
    return {
        "day": start.astimezone(zone).date().isoformat(),
        "span": _span(zone, start, end),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "source": printable(item.source),
        "confidence": item.confidence.value,
    }


def serialize_availability(view: ExternalView) -> Json:
    zone = view.zone
    ordered = sorted(view.external, key=lambda b: b.interval.start.timestamp())
    return {
        "week": view.week.render(),
        "zone": zone.key,
        "plan_path": view.path,
        "plan_status": view.status.value if view.status is not None else None,
        "busy": [_busy(item, zone) for item in ordered],
        "clashes": [
            {"block": serialize.block(block, zone), "busy": _busy(hit, zone)}
            for block, hit in view.clashes
        ],
    }


@router.get("")
def estado(request: Request) -> JSONResponse:
    web: WebRuntime = request.app.state.web
    if not web.settings.environ.get(GOOGLE_CLIENT_ID_ENV):
        return ok({"configured": False, "grants": [], "can_read": False})
    statuses = calendar_access(web.settings).status()
    tools = web.base.calendar
    return ok(
        {
            "configured": True,
            "grants": [_grant(s) for s in statuses],
            # Los permisos se leen al arrancar: uno conectado después pide reiniciar lifeos-web.
            "can_read": tools is not None and tools.can_read,
        }
    )


@router.get("/disponibilidad")
def disponibilidad(
    request: Request,
    semana: Annotated[str | None, Query(max_length=WEEK_CHARS)] = None,
) -> JSONResponse:
    week = IsoWeek.parse(semana) if semana else None
    cache = _cache(request)
    key = str(week) if week is not None else ""
    cached = cache.get(key)
    if cached is not None:
        return ok(cached)
    web: WebRuntime = request.app.state.web
    data = serialize_availability(jefe(web.base).eventos_externos(week))
    cache.put(key, data)
    return ok(data)
