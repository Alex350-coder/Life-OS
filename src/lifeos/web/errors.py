"""Respuestas JSON con un sobre común y traducción de errores, con los mismos casos que la CLI.

Sobre: `{"ok": true, "data": …}` o `{"ok": false, "error": {"code", "message"}}`. Los mensajes
pasan por `printable`; los de validación dicen el campo y el motivo, nunca el valor recibido.
Los errores inesperados los responde la guardia de acceso (`middleware.py`), dentro de sus
cabeceras de seguridad y sin detalles.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from lifeos.application.closing import ClosingIncompleteError
from lifeos.application.llm_calls import UsageNotRecordedError
from lifeos.application.plan_publication import PartialPublicationError
from lifeos.application.tools import AuditIncompleteError
from lifeos.bootstrap import STARTUP_ERRORS
from lifeos.domain.errors import LifeOSError, ValidationError
from lifeos.domain.text import printable
from lifeos.ports.llm import LlmError
from lifeos.ports.logs import LogWriteError
from lifeos.ports.vault import VaultIOError
from lifeos.web.runtime import RateLimitedError, SetupError

MAX_MESSAGE_CHARS = 500
INTERNAL_MESSAGE = "error interno; revisa la terminal de lifeos-web"


def ok(data: object, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def envelope(code: str, message: str, *, data: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ok": False,
        "error": {"code": code, "message": printable(message)[:MAX_MESSAGE_CHARS]},
    }
    if data is not None:
        body["data"] = data
    return body


def fail(
    code: str,
    message: str,
    status: int,
    *,
    data: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(envelope(code, message, data=data), status_code=status, headers=headers)


def _field(error: dict[str, Any]) -> str:
    return ".".join(str(part) for part in error.get("loc", ()) if part != "body") or "petición"


async def _invalid_request(_: Request, exc: Exception) -> JSONResponse:
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    first = errors[0] if errors else {}
    return fail("entrada_invalida", f"{_field(first)}: {first.get('msg', 'no válido')}", 422)


async def _http(_: Request, exc: Exception) -> JSONResponse:
    status = exc.status_code if isinstance(exc, HTTPException) else 500
    detail = exc.detail if isinstance(exc, HTTPException) else INTERNAL_MESSAGE
    headers = exc.headers if isinstance(exc, HTTPException) else None
    return fail("http", str(detail), status, headers=dict(headers) if headers else None)


async def _config(_: Request, exc: Exception) -> JSONResponse:
    return fail("configuracion", f"error de configuración: {exc}", 503)


async def _rate(_: Request, exc: Exception) -> JSONResponse:
    retry = exc.retry_after if isinstance(exc, RateLimitedError) else 60
    return fail("limite_tasa", str(exc), 429, headers={"retry-after": str(retry)})


async def _audit_incomplete(_: Request, exc: Exception) -> JSONResponse:
    return fail(
        "auditoria_incompleta", f"la escritura SÍ se hizo, pero no quedó auditada: {exc}", 500
    )


async def _partial(_: Request, exc: Exception) -> JSONResponse:
    written = getattr(exc, "written", ())
    return fail("escritura_parcial", str(exc), 500, data={"written": [str(w) for w in written]})


async def _usage_not_recorded(_: Request, exc: Exception) -> JSONResponse:
    return fail(
        "uso_no_registrado",
        f"el modelo respondió (y se cobró), pero no se pudo registrar el uso: {exc}",
        502,
    )


async def _io(_: Request, exc: Exception) -> JSONResponse:
    return fail("io", f"no se pudo leer o escribir: {exc}", 503)


async def _llm(_: Request, exc: Exception) -> JSONResponse:
    return fail("modelo", f"error del modelo: {exc}. Puedes repetirlo sin IA.", 502)


async def _validation(_: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, ValidationError):
        return fail("entrada_invalida", f"{exc.field}: {exc.reason}", 422)
    return fail("entrada_invalida", "valor no válido", 422)


async def _refused(_: Request, exc: Exception) -> JSONResponse:
    return fail("rechazado", str(exc), 409)


def install_error_handlers(app: FastAPI) -> None:
    """Starlette elige el manejador por la jerarquía de la excepción (el más específico)."""
    app.add_exception_handler(RequestValidationError, _invalid_request)
    app.add_exception_handler(HTTPException, _http)
    config_errors: tuple[type[Exception], ...] = (SetupError, *STARTUP_ERRORS)
    for kind in config_errors:
        app.add_exception_handler(kind, _config)
    app.add_exception_handler(RateLimitedError, _rate)
    app.add_exception_handler(AuditIncompleteError, _audit_incomplete)
    app.add_exception_handler(PartialPublicationError, _partial)
    app.add_exception_handler(ClosingIncompleteError, _partial)
    app.add_exception_handler(UsageNotRecordedError, _usage_not_recorded)
    app.add_exception_handler(LogWriteError, _io)
    app.add_exception_handler(VaultIOError, _io)
    app.add_exception_handler(LlmError, _llm)
    app.add_exception_handler(ValidationError, _validation)
    app.add_exception_handler(LifeOSError, _refused)
