"""HTTP mínimo hacia Google sobre `urllib` (ADR-0017, mismo enfoque que ADR-0007 y ADR-0014).

Garantías: solo `https` hacia hosts de Google (o `http` hacia 127.0.0.1 en pruebas), sin
redirecciones, sin proxy en pruebas locales y respuestas de 1 MiB como máximo. Ningún error
incluye la URL, el cuerpo enviado ni un token: solo el código HTTP y, si Google lo da, su
código de error corto (`invalid_grant`, `rateLimitExceeded`…).
"""

import http.client
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.client import HTTPMessage
from typing import IO
from urllib.parse import urlencode, urlsplit

from lifeos.adapters.secrets import Secret
from lifeos.ports.calendar import CalendarError

GOOGLE_HOSTS = frozenset({"accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com"})
LOOPBACK = "127.0.0.1"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024
DEFAULT_TIMEOUT_S = 20
_CHUNK_BYTES = 64 * 1024
_CODE_RE = re.compile(r"[A-Za-z_]{1,64}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        return None


def _allowed(url: str) -> bool:
    parts = urlsplit(url)
    official = parts.scheme == "https" and parts.hostname in GOOGLE_HOSTS
    loopback = parts.scheme == "http" and parts.hostname == LOOPBACK
    return (official or loopback) and not parts.username and not parts.password


def check_base_url(base_url: str) -> str:
    """Un token solo viaja a Google (o a 127.0.0.1 en pruebas)."""
    if not _allowed(base_url) or urlsplit(base_url).query:
        raise ValueError("Google debe usarse por https (http solo hacia 127.0.0.1 en pruebas)")
    return base_url.rstrip("/")


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    data: dict[str, object]

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def error_code(self) -> str | None:
        """`invalid_grant` (OAuth) o la `reason` del primer error de la API, si es corto."""
        raw = self.data.get("error")
        if isinstance(raw, str):
            return raw if _CODE_RE.fullmatch(raw) else None
        if isinstance(raw, dict):
            errors = raw.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                reason = errors[0].get("reason")
                if isinstance(reason, str) and _CODE_RE.fullmatch(reason):
                    return reason
            status = raw.get("status")
            if isinstance(status, str) and _CODE_RE.fullmatch(status):
                return status
        return None

    def describe(self) -> str:
        code = self.error_code()
        return f"Google respondió {self.status}" + (f" ({code})" if code else "")


def _read(stream: IO[bytes], limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    reader = getattr(stream, "read1", stream.read)
    while chunk := reader(_CHUNK_BYTES):
        size += len(chunk)
        if size > limit:
            raise CalendarError(f"respuesta de Google demasiado grande (más de {limit} bytes)")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse(raw: bytes) -> dict[str, object]:
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise CalendarError("Google no devolvió JSON válido") from None
    if not isinstance(data, dict):
        raise CalendarError("Google devolvió una respuesta inesperada")
    return data


class GoogleHttp:
    def __init__(self, *, loopback: bool = False) -> None:
        handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
        if loopback:
            handlers.append(urllib.request.ProxyHandler({}))
        self._opener = urllib.request.build_opener(*handlers)

    def request(
        self,
        method: str,
        url: str,
        *,
        form: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
        bearer: Secret | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> Response:
        if not _allowed(url):
            raise CalendarError("destino no permitido: solo Google (o 127.0.0.1 en pruebas)")
        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if form is not None:
            data = urlencode(form).encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer.reveal()}"
        request = urllib.request.Request(  # noqa: S310 - URL validada arriba
            url, data=data, method=method, headers=headers
        )
        return self._open(request, timeout_s)

    def _open(self, request: urllib.request.Request, timeout_s: float) -> Response:
        try:
            with self._opener.open(request, timeout=timeout_s) as response:
                return Response(response.status, _parse(_read(response, MAX_RESPONSE_BYTES)))
        except urllib.error.HTTPError as error:
            try:
                data = _parse(_read(error, MAX_ERROR_BYTES))
            except CalendarError:
                data = {}  # un cuerpo de error ilegible no oculta el código HTTP
            finally:
                error.close()
            return Response(error.code, data)
        except urllib.error.URLError as error:
            name = type(error.reason).__name__
            raise CalendarError(f"no se pudo conectar con Google ({name})") from None
        except (TimeoutError, http.client.HTTPException, OSError) as error:
            raise CalendarError(
                f"conexión con Google interrumpida ({type(error).__name__})"
            ) from None
