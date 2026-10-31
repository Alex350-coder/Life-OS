"""Rutas de la API local (F5-T01…T04). Cada una traduce la petición, llama a un caso de uso ya
existente y devuelve su resultado; ninguna contiene reglas de negocio (ADR-0006).

Las mismas órdenes que la CLI: `capturar`, `tareas`, `semana`, `aprobar`, `semana
--replanificar`, `hoy`, `checkin`, `cierre` y `uso`, más las lecturas de la web (próximas,
historial y agentes).
"""

import re
from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from lifeos.application.agents_overview import agents_overview
from lifeos.application.capture import capture
from lifeos.application.history import DEFAULT_LIMIT, MAX_LIMIT, recent_changes
from lifeos.application.jefe import PlanOutcome
from lifeos.application.tasks import MAX_UPCOMING_DAYS, TaskQuery, query_tasks, upcoming_tasks
from lifeos.application.usage_report import summarize
from lifeos.bootstrap import Runtime
from lifeos.domain.closing import AnswerKind, ClosingAnswer
from lifeos.domain.coach import CheckInState
from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.plan import PlanOrigin
from lifeos.domain.task import Area, Effort, Progress, TaskStatus
from lifeos.domain.text import printable
from lifeos.domain.weeks import IsoWeek
from lifeos.web import serialize
from lifeos.web.errors import fail, ok
from lifeos.web.middleware import SESSION_SCOPE_KEY
from lifeos.web.runtime import (
    AI_RATE_LIMIT,
    WebRuntime,
    closing,
    coach,
    jefe,
    vault,
)
from lifeos.web.schemas import (
    AnswerIn,
    ApprovalIn,
    CaptureIn,
    CheckInIn,
    ClosingIn,
    PlanIn,
)
from lifeos.web.security import SessionStore

WEB_SOURCE = "web"
__all__ = ["AI_RATE_LIMIT", "router"]
MAX_QUERY = 200
_MONTH_RE = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")

router = APIRouter(prefix="/api")


def _web(request: Request) -> WebRuntime:
    web: WebRuntime = request.app.state.web
    return web


def _base(request: Request) -> Runtime:
    return _web(request).base


def _choice[E: (Area, TaskStatus, CheckInState)](kind: type[E], value: str, field: str) -> E:
    try:
        return kind(value)
    except ValueError:
        allowed = ", ".join(item.value for item in kind)
        raise ValidationError(field, value, f"valores posibles: {allowed}") from None


def _week(text: str | None) -> IsoWeek | None:
    return IsoWeek.parse(text) if text else None


def _task_id(text: str | None) -> TaskId | None:
    return TaskId(text) if text else None


def _month(request: Request, text: str | None) -> tuple[int, int]:
    if text:
        match = _MONTH_RE.fullmatch(text)
        if match is None:
            raise ValidationError("mes", text, "formato esperado AAAA-MM (mes 01 a 12)")
        return int(match[1]), int(match[2])
    now = _base(request).clock.now().astimezone(UTC)
    return now.year, now.month


def _plan(result: PlanOutcome) -> JSONResponse:
    data = serialize.plan(result)
    if result.accepted:
        return ok(data)
    message = f"{len(result.conflicts)} conflicto(s); no se guardó nada"
    return fail("plan_rechazado", message, 422, data=data)


# --- Sesión ----------------------------------------------------------------------------------


@router.get("/session")
def session(request: Request) -> JSONResponse:
    current = request.scope["state"][SESSION_SCOPE_KEY]
    return ok({"csrf_token": current.csrf})


@router.post("/logout", status_code=204)
def logout(request: Request) -> Response:
    sessions: SessionStore = request.app.state.sessions
    sessions.revoke(request.scope["state"][SESSION_SCOPE_KEY].id)
    return Response(status_code=204)


# --- Hoy, Semana y planes (Jefe) -------------------------------------------------------------


@router.get("/hoy")
def hoy(request: Request) -> JSONResponse:
    return ok(serialize.briefing(coach(_base(request)).briefing()))


@router.get("/semana")
def semana(
    request: Request, semana: Annotated[str | None, Query(max_length=10)] = None
) -> JSONResponse:
    return ok(serialize.week(jefe(_base(request)).semana(_week(semana))))


@router.post("/plan/propuesta")
def proponer(request: Request, body: PlanIn) -> JSONResponse:
    web = _web(request)
    origin = PlanOrigin.IA if body.ia else PlanOrigin.HEURISTICA
    with web.writing():
        result = jefe(web.runtime(use_ai=body.ia)).proponer(_week(body.semana), origin=origin)
    return _plan(result)


@router.post("/plan/aprobacion")
def aprobar(request: Request, body: ApprovalIn) -> JSONResponse:
    web = _web(request)
    with web.writing():
        result = jefe(web.base).aprobar(_week(body.semana))
    return _plan(result)


@router.post("/plan/revision")
def replanificar(request: Request, body: PlanIn) -> JSONResponse:
    web = _web(request)
    origin = PlanOrigin.IA if body.ia else PlanOrigin.HEURISTICA
    with web.writing():
        result = jefe(web.runtime(use_ai=body.ia)).replanificar(_week(body.semana), origin=origin)
    return _plan(result)


# --- Coach y cierre --------------------------------------------------------------------------


@router.post("/checkins", status_code=201)
def checkin(request: Request, body: CheckInIn) -> JSONResponse:
    web = _web(request)
    state = _choice(CheckInState, body.estado, "estado")
    task_id = _task_id(body.tarea)
    with web.writing():
        result = coach(web.runtime(use_ai=body.ia)).checkin(
            body.texto, state=state, task_id=task_id, capture=body.capturar or None, use_ai=body.ia
        )
    return ok(serialize.checkin(result), 201)


def _answer(item: AnswerIn) -> ClosingAnswer:
    task_id = TaskId(item.tarea)
    progress = None
    if item.avance:
        raw = item.avance if item.avance.endswith("%") else f"{item.avance}%"
        progress = Progress.parse(raw)
    remaining = Effort.parse(item.restante) if item.restante else None
    return ClosingAnswer(task_id, AnswerKind(item.tipo), progress, remaining)


@router.post("/cierre/vista-previa")
def cierre_vista_previa(request: Request, body: ClosingIn) -> JSONResponse:
    answers = [_answer(item) for item in body.respuestas]
    return ok(serialize.closing_preview(closing(_base(request)).preview(answers)))


@router.post("/cierre")
def cierre(request: Request, body: ClosingIn) -> JSONResponse:
    web = _web(request)
    answers = [_answer(item) for item in body.respuestas]
    with web.writing():
        result = closing(web.base).apply(answers)
    return ok(serialize.closing_result(result))


# --- Tareas y capturas -----------------------------------------------------------------------


@router.post("/capturas", status_code=201)
def capturar(request: Request, body: CaptureIn) -> JSONResponse:
    web = _web(request)
    runtime = web.base
    with web.writing():
        path = capture(vault(runtime), body.texto, now=runtime.clock.now(), source=WEB_SOURCE)
    return ok({"path": printable(str(path))}, 201)


@router.get("/tareas")
def tareas(
    request: Request,
    area: Annotated[str | None, Query(max_length=40)] = None,
    estado: Annotated[str | None, Query(max_length=40)] = None,
    texto: Annotated[str | None, Query(max_length=MAX_QUERY)] = None,
    archivadas: bool = False,
) -> JSONResponse:
    query = TaskQuery(
        area=_choice(Area, area, "area") if area else None,
        status=_choice(TaskStatus, estado, "estado") if estado else None,
        text=texto or None,
        include_archived=archivadas,
    )
    return ok(serialize.tasks(query_tasks(vault(_base(request)), query)))


@router.get("/tareas/proximas")
def proximas(
    request: Request, dias: Annotated[int, Query(ge=1, le=MAX_UPCOMING_DAYS)] = 14
) -> JSONResponse:
    runtime = _base(request)
    today = coach(runtime).today()
    return ok(serialize.tasks(upcoming_tasks(vault(runtime), today, days=dias)))


# --- Historial, agentes y uso (solo lectura) -------------------------------------------------


@router.get("/historial")
def historial(
    request: Request, limite: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT
) -> JSONResponse:
    runtime = _base(request)
    changes = recent_changes(runtime.audit_reader, runtime.clock.now(), limit=limite)
    return ok(serialize.history(changes))


@router.get("/agentes")
def agentes(
    request: Request, mes: Annotated[str | None, Query(max_length=7)] = None
) -> JSONResponse:
    runtime = _base(request)
    year, month = _month(request, mes)
    usage = summarize(runtime.usage_reader.read_month(year, month))
    overview = agents_overview(
        runtime.agents, runtime.permissions, runtime.policy, runtime.catalog, usage
    )
    return ok(serialize.agents(overview, f"{year:04d}-{month:02d}"))


@router.get("/uso")
def uso(request: Request, mes: Annotated[str | None, Query(max_length=7)] = None) -> JSONResponse:
    runtime = _base(request)
    year, month = _month(request, mes)
    summary = summarize(runtime.usage_reader.read_month(year, month))
    return ok(serialize.usage(summary, f"{year:04d}-{month:02d}"))
