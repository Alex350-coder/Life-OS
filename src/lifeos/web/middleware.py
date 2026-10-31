"""Guardia de acceso (ADR-0013, SEC-11) como middleware ASGI puro, antes de cualquier ruta.

Orden: Host permitido (400) → Origin permitido (403; obligatorio si cambia estado) → sin
preflight CORS (403) → límite de tasa (429) → sesión para `/api/*` (401) → CSRF si cambia
estado (403). Todas las respuestas llevan las cabeceras de seguridad.
"""

import logging
import secrets
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse

from lifeos.web.security import (
    API_RATE_LIMIT,
    AUTH_RATE_LIMIT,
    CSRF_HEADER,
    SESSION_COOKIE,
    Monotonic,
    RateLimiter,
    SessionStore,
    allowed_hosts,
    allowed_origins,
    same_secret,
)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
App = Callable[[Scope, Receive, Send], Awaitable[None]]

AUTH_PATH = "/auth"
API_PREFIX = "/api/"
SAFE_METHODS = frozenset({"GET", "HEAD"})
SESSION_SCOPE_KEY = "lifeos.session"
MAX_BODY_BYTES = 64 * 1024
# `none`: navegación directa (la URL de arranque); `same-origin`: la propia página.
ALLOWED_FETCH_SITES = frozenset({"none", "same-origin"})
LOGGER = logging.getLogger("lifeos.web")
# Sin scripts ni estilos en línea ni de terceros (ADR-0012): todo sale de los estáticos locales.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = (
    ("content-security-policy", CSP),
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "no-referrer"),
    ("cross-origin-opener-policy", "same-origin"),
    ("cross-origin-resource-policy", "same-origin"),
    ("permissions-policy", "camera=(), microphone=(), geolocation=()"),
)


def _error(code: str, message: str, status: int, headers: dict[str, str] | None = None) -> App:
    return JSONResponse(
        {"ok": False, "error": {"code": code, "message": message}},
        status_code=status,
        headers=headers,
    )


class AccessGuard:
    def __init__(
        self, app: App, *, port: int, sessions: SessionStore, monotonic: Monotonic
    ) -> None:
        self._app = app
        self._hosts = allowed_hosts(port)
        self._origins = allowed_origins(port)
        self._sessions = sessions
        self._auth_rate = RateLimiter(monotonic, limit=AUTH_RATE_LIMIT)
        self._api_rate = RateLimiter(monotonic, limit=API_RATE_LIMIT)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    headers[name] = value
                if scope["path"].startswith(API_PREFIX) or scope["path"] == AUTH_PATH:
                    headers["cache-control"] = "no-store"
            await send(message)

        rejection = self._check(scope)
        if rejection is not None:
            await rejection(scope, receive, send_with_headers)
            return
        try:
            await self._app(scope, _limited(receive), send_with_headers)
        except BodyTooLargeError:
            if not started:
                await _error("tamano", "la petición es demasiado grande", 413)(
                    scope, receive, send_with_headers
                )
        except Exception as error:
            # Solo el tipo y un identificador: el mensaje puede contener rutas o datos de notas.
            incident = secrets.token_hex(4)
            LOGGER.error(
                "error interno %s en %s: %s", incident, scope["path"], type(error).__name__
            )
            if not started:
                message = f"error interno ({incident}); revisa la terminal de lifeos-web"
                await _error("interno", message, 500)(scope, receive, send_with_headers)

    def _check(self, scope: Scope) -> App | None:
        headers = Headers(scope=scope)
        method = scope["method"]
        path: str = scope["path"]
        if headers.get("host") not in self._hosts:
            return _error("host", "host no permitido", 400)
        origin = headers.get("origin")
        changes_state = method not in SAFE_METHODS
        if origin is not None and origin not in self._origins:
            return _error("origen", "origen no permitido", 403)
        if method == "OPTIONS":
            return _error("origen", "CORS no está habilitado", 403)
        if changes_state and origin is None:
            return _error("origen", "falta la cabecera Origin", 403)
        guarded = path == AUTH_PATH or path.startswith(API_PREFIX)
        # Otra web (aunque sea otro puerto de localhost) nunca llega a la API ni al acceso.
        if guarded and headers.get("sec-fetch-site", "none") not in ALLOWED_FETCH_SITES:
            return _error("origen", "petición desde otro sitio", 403)
        if _declared_length(headers) > MAX_BODY_BYTES:
            return _error("tamano", "la petición es demasiado grande", 413)
        if path == AUTH_PATH:
            return self._rate(self._auth_rate, "auth")
        if not path.startswith(API_PREFIX):
            return None
        session = self._sessions.get(cookie_parser(headers.get("cookie", "")).get(SESSION_COOKIE))
        if session is None:
            return _error("sin_sesion", "abre la URL que muestra lifeos-web al arrancar", 401)
        # Se cuenta por sesión y solo con sesión válida: nadie más puede agotar el cupo.
        limited = self._rate(self._api_rate, session.id)
        if limited is not None:
            return limited
        if changes_state and not same_secret(headers.get(CSRF_HEADER, ""), session.csrf):
            return _error("csrf", "falta o no coincide el token CSRF", 403)
        scope.setdefault("state", {})[SESSION_SCOPE_KEY] = session
        return None

    @staticmethod
    def _rate(limiter: RateLimiter, key: str) -> App | None:
        allowed, retry = limiter.allow(key)
        if allowed:
            return None
        return _error(
            "limite_tasa",
            "demasiadas peticiones; espera un momento",
            429,
            {"retry-after": str(retry)},
        )


class BodyTooLargeError(Exception):
    """El cuerpo recibido supera `MAX_BODY_BYTES` (aunque no declare su longitud)."""


def _declared_length(headers: Headers) -> int:
    raw = headers.get("content-length", "0")
    return int(raw) if raw.isdigit() else MAX_BODY_BYTES + 1


def _limited(receive: Receive) -> Receive:
    received = 0

    async def wrapped() -> Message:
        nonlocal received
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > MAX_BODY_BYTES:
                raise BodyTooLargeError
        return message

    return wrapped
