"""Aplicación FastAPI de la web local (ADR-0012, ADR-0013).

`create_app` valida la configuración al construirse (como la CLI al arrancar), monta la API, la
autenticación con el token de arranque y los estáticos del frontend compilado. La guardia de
acceso envuelve todo, incluidos los estáticos.
"""

import re
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


# Segmento de ruta de un estático: solo nombres simples (sin `\`, `:`, `%`, unidades ni UNC).
_SEGMENT_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,100}")
MAX_SEGMENTS = 8


def _dist_file(root: Path, path: str) -> Path | None:
    """Archivo de `dist/` para `path`, validando el texto ANTES de tocar el disco: una ruta UNC
    (como `//host/share` con barras invertidas) o absoluta haría que Windows intentara
    conectarse a otro equipo."""
    parts = path.split("/")
    if not path or len(parts) > MAX_SEGMENTS or not all(_SEGMENT_RE.fullmatch(p) for p in parts):
        return None
    candidate = root.joinpath(*parts)
    return candidate if candidate.is_file() else None


def _static(app: FastAPI, dist: Path) -> None:
    root = dist.absolute()

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def frontend(path: str) -> Response:
        if path.startswith("api/") or path == "api":
            return fail("no_encontrado", "ruta de la API desconocida", 404)
        index = root / INDEX
        if not index.is_file():
            return PlainTextResponse(NOT_BUILT, status_code=503)
        # Un archivo compilado o, para cualquier otra ruta, la SPA.
        return FileResponse(_dist_file(root, path) or index)


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
    app.state.web = WebRuntime(settings, clock=clock, monotonic=monotonic)
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
