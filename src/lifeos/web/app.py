"""Aplicación FastAPI de la web local (ADR-0012, ADR-0013).

`create_app` valida la configuración al construirse (como la CLI al arrancar), monta la API, la
autenticación con el token de arranque y los estáticos del frontend compilado. La guardia de
acceso envuelve todo, incluidos los estáticos.
"""

import time
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response

from lifeos.bootstrap import Settings
from lifeos.ports.clock import Clock
from lifeos.web.errors import fail, install_error_handlers
from lifeos.web.middleware import AUTH_PATH, AccessGuard
from lifeos.web.routes import router
from lifeos.web.runtime import WebRuntime
from lifeos.web.security import (
    SESSION_COOKIE,
    SESSION_TTL_S,
    LaunchToken,
    Monotonic,
    SessionStore,
)

MAX_TOKEN_CHARS = 200
NOT_BUILT = (
    "La interfaz web no está compilada. En project/web/ ejecuta: npm ci && npm run build\n"
    "y vuelve a abrir esta página."
)
INDEX = "index.html"


def _static(app: FastAPI, dist: Path) -> None:
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> Response:
        if path.startswith("api/"):
            return fail("no_encontrado", "ruta de la API desconocida", 404)
        index = root / INDEX
        if not index.is_file():
            return PlainTextResponse(NOT_BUILT, status_code=503)
        candidate = (root / path).resolve() if path else index
        # Solo archivos dentro de dist/ (sin traversal); cualquier otra ruta es la SPA.
        if candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)


def create_app(
    settings: Settings,
    *,
    port: int,
    clock: Clock | None = None,
    monotonic: Monotonic = time.monotonic,
    launch_token: LaunchToken,
    dist_dir: Path,
) -> FastAPI:
    app = FastAPI(
        title="Life OS (local)",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    sessions = SessionStore(monotonic)
    app.state.web = WebRuntime(settings, clock=clock)
    app.state.sessions = sessions
    install_error_handlers(app)

    @app.get(AUTH_PATH, include_in_schema=False)
    def auth(
        token: Annotated[str | None, Query(max_length=MAX_TOKEN_CHARS)] = None,
    ) -> Response:
        if token is None or not launch_token.consume(token):
            return fail("token", "token de arranque no válido, usado o caducado", 401)
        session = sessions.create()
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            session.id,
            max_age=SESSION_TTL_S,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    app.include_router(router)
    _static(app, dist_dir)
    app.add_middleware(AccessGuard, port=port, sessions=sessions, monotonic=monotonic)
    return app
