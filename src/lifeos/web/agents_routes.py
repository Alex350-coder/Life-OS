"""Rutas del registro de agentes y de las propuestas (Fase 8, PLAN §9). Traducen la petición y
llaman a los casos de uso; ninguna contiene reglas (ADR-0006). Sesión, CSRF y Origin los aplica
el middleware a todas las rutas `/api` (SEC-11)."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse

from lifeos.application.agent_registry import AgentRegistry
from lifeos.application.proposal_review import ProposalReview
from lifeos.application.specialists import SpecialistRunner
from lifeos.bootstrap import Runtime
from lifeos.domain.agent_lifecycle import LifecycleAction
from lifeos.domain.area_proposals import ProposalRef
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.config import ConfigError
from lifeos.web import agents_serialize as serialize
from lifeos.web.errors import fail, ok
from lifeos.web.runtime import SetupError, WebRuntime
from lifeos.web.schemas import AgentActionIn, AgentImportIn, AgentRunIn, ProposalRefIn

router = APIRouter(prefix="/api")
AgentId = Annotated[str, Path(pattern=r"^[a-z0-9-]{1,32}$")]


def _web(request: Request) -> WebRuntime:
    web: WebRuntime = request.app.state.web
    return web


def _registry(runtime: Runtime) -> AgentRegistry:
    if runtime.registry is None:
        raise SetupError("registro de agentes no disponible")
    return runtime.registry


def _runner(runtime: Runtime) -> SpecialistRunner:
    if runtime.specialists is None:
        raise SetupError("falta LIFEOS_VAULT_PATH: los especialistas leen la bóveda")
    return runtime.specialists


def _review(runtime: Runtime) -> ProposalReview:
    if runtime.proposals is None:
        raise SetupError("falta LIFEOS_VAULT_PATH: las propuestas viven en la bóveda")
    return runtime.proposals


def imported(runtime: Runtime) -> list[serialize.Json]:
    """Agentes importados para la vista de Agentes (`GET /api/agentes`)."""
    if runtime.registry is None:
        return []
    return [serialize.registry_view(view) for view in runtime.registry.views()]


@router.post("/agentes/importacion", status_code=201)
def importar(request: Request, body: AgentImportIn) -> JSONResponse:
    web = _web(request)
    registry = _registry(web.base)
    try:
        with web.writing():
            entry = registry.import_config(body.contenido.encode("utf-8"), body.nombre)
    except ConfigError as error:
        return fail(
            "agente_rechazado",
            "la configuración no es válida o pide más de lo que permite la política",
            422,
            data={"violations": [serialize.text(v) for v in error.violations]},
        )
    return ok(serialize.registry_view(registry.view(entry.agent_id)), 201)


@router.post("/agentes/{agent_id}/prueba")
def probar(request: Request, agent_id: AgentId, body: AgentRunIn) -> JSONResponse:
    web = _web(request)
    with web.writing(use_ai=body.ia):
        result = _runner(web.runtime(use_ai=body.ia)).run(agent_id, dry_run=True)
    return ok(serialize.run_result(result))


@router.post("/agentes/{agent_id}/estado")
def cambiar_estado(request: Request, agent_id: AgentId, body: AgentActionIn) -> JSONResponse:
    web = _web(request)
    registry = _registry(web.base)
    with web.writing():
        registry.transition(agent_id, LifecycleAction(body.accion), version=body.version)
    return ok(serialize.registry_view(registry.view(agent_id)))


@router.get("/agentes/{agent_id}/exportacion")
def exportar(
    request: Request,
    agent_id: AgentId,
    version: Annotated[int | None, Query(ge=1, le=1000)] = None,
) -> JSONResponse:
    registry = _registry(_web(request).base)
    data = registry.export(agent_id, version=version)
    current = registry.view(agent_id).entry.current
    return ok({"version": version or current, "contenido": data.decode("utf-8")})


@router.post("/agentes/{agent_id}/ejecucion")
def ejecutar(request: Request, agent_id: AgentId, body: AgentRunIn) -> JSONResponse:
    web = _web(request)
    week = IsoWeek.parse(body.semana) if body.semana else None
    with web.writing(use_ai=body.ia):
        result = _runner(web.runtime(use_ai=body.ia)).run(agent_id, week=week)
    return ok(serialize.run_result(result))


@router.get("/propuestas")
def propuestas(
    request: Request, semana: Annotated[str | None, Query(max_length=10)] = None
) -> JSONResponse:
    week = IsoWeek.parse(semana) if semana else None
    pending = _review(_web(request).base).pending(week)
    return ok(
        {
            "items": [serialize.review_item(i) for i in pending.items],
            "skipped": [serialize.text(s) for s in pending.skipped],
        }
    )


@router.post("/propuestas/aceptacion")
def aceptar(request: Request, body: ProposalRefIn) -> JSONResponse:
    web = _web(request)
    ref = ProposalRef.parse(body.ref)
    with web.writing():
        result = _review(web.base).accept(ref)
    return ok(serialize.accept_result(result))


@router.post("/propuestas/rechazo")
def rechazar(request: Request, body: ProposalRefIn) -> JSONResponse:
    web = _web(request)
    ref = ProposalRef.parse(body.ref)
    with web.writing():
        _review(web.base).reject(ref)
    return ok({"ref": str(ref)})
