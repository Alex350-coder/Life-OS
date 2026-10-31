"""Respuestas JSON con un sobre común y traducción de errores, con los mismos casos que la CLI.

Sobre: `{"ok": true, "data": …}` o `{"ok": false, "error": {"code", "message"}}`. Los mensajes
pasan por `printable` y nunca incluyen secretos ni el texto enviado en la petición.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from lifeos.application.tools import AuditIncompleteError
from lifeos.bootstrap import STARTUP_ERRORS
from lifeos.domain.errors import LifeOSError, ValidationError
from lifeos.domain.text import printable
from lifeos.ports.llm import LlmError
from lifeos.web.runtime import SetupError

LOGGER = logging.getLogger("lifeos.web")
MAX_MESSAGE_CHARS = 500


def ok(data: object, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def fail(code: str, message: str, status: int, *, data: Any = None) -> JSONResponse:
    body: dict[str, Any] = {
        "ok": False,
        "error": {"code": code, "message": printable(message)[:MAX_MESSAGE_CHARS]},
    }
    if data is not None:
        body["data"] = data
    return JSONResponse(body, status_code=status)


def _field(error: dict[str, Any]) -> str:
    return ".".join(str(part) for part in error.get("loc", ()) if part != "body") or "petición"


async def _invalid_request(_: Request, exc: Exception) -> JSONResponse:
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    first = errors[0] if errors else {}
    # Solo el campo y el motivo: el valor recibido no se repite en la respuesta.
    return fail("entrada_invalida", f"{_field(first)}: {first.get('msg', 'no válido')}", 422)


async def _config(_: Request, exc: Exception) -> JSONResponse:
    return fail("configuracion", f"error de configuración: {exc}", 503)


async def _audit_incomplete(_: Request, exc: Exception) -> JSONResponse:
    return fail(
        "auditoria_incompleta", f"la escritura SÍ se hizo, pero no quedó auditada: {exc}", 500
    )


async def _llm(_: Request, exc: Exception) -> JSONResponse:
    return fail("modelo", f"error del modelo: {exc}. Puedes repetirlo sin IA.", 502)


async def _validation(_: Request, exc: Exception) -> JSONResponse:
    return fail("entrada_invalida", str(exc), 422)


async def _refused(_: Request, exc: Exception) -> JSONResponse:
    return fail("rechazado", str(exc), 409)


async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
    LOGGER.error("error interno de la web: %s", type(exc).__name__)
    return fail("interno", "error interno; revisa la terminal de lifeos-web", 500)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _invalid_request)
    config_errors: tuple[type[Exception], ...] = (SetupError, *STARTUP_ERRORS)
    for kind in config_errors:
        app.add_exception_handler(kind, _config)
    app.add_exception_handler(AuditIncompleteError, _audit_incomplete)
    app.add_exception_handler(LlmError, _llm)
    app.add_exception_handler(ValidationError, _validation)
    app.add_exception_handler(LifeOSError, _refused)
    app.add_exception_handler(Exception, _unexpected)
