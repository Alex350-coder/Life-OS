"""Cliente del catálogo de NVIDIA (endpoint compatible con OpenAI) sobre `urllib` (ADR-0007).

Garantías: solo `https` (o `http` hacia 127.0.0.1 en pruebas), sin redirecciones, la clave
viaja en una cabecera que `urllib` no reenvía, plazo total por llamada además del timeout por
operación de socket, respuesta limitada a 1 MiB y errores que nunca incluyen el cuerpo del
proveedor ni la clave. Un `202` (resultado pendiente) no se consulta: se informa como error.
"""

import email.utils
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from http.client import HTTPMessage, HTTPResponse
from typing import IO, Any
from urllib.parse import urlsplit

from lifeos.adapters.secrets import Secret
from lifeos.ports.llm import (
    ChatMessage,
    LlmConnectionError,
    LlmProviderError,
    LlmRateLimitError,
    LlmRequest,
    LlmResponse,
    LlmResponseError,
    LlmTimeoutError,
    TokenUsage,
)

PROVIDER = "nvidia"
MAX_RESPONSE_BYTES = 1024 * 1024
_CHUNK_BYTES = 64 * 1024
_RETRYABLE_STATUSES = frozenset({502, 503, 504})
_LOOPBACK = "127.0.0.1"
_MAX_RETRY_AFTER_DIGITS = 10


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """Segundos de espera de `Retry-After` (entero o fecha HTTP); `None` si falta o no es válido."""
    if value is None:
        return None
    text = value.strip()
    if text.isascii() and text.isdigit():
        return float(int(text)) if len(text) <= _MAX_RETRY_AFTER_DIGITS else None
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, (when - now).total_seconds())


def _check_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    secure = parts.scheme == "https" and bool(parts.hostname)
    loopback = parts.scheme == "http" and parts.hostname == _LOOPBACK
    if not (secure or loopback):
        raise ValueError("el proveedor debe usar https (http solo hacia 127.0.0.1 en pruebas)")
    return base_url.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Una redirección podría llevar la petición (y su coste) a otro destino: no se sigue."""

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


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _reject_constant(name: str) -> object:
    raise ValueError(f"constante JSON no permitida: {name}")


def _parse_envelope(raw: bytes, fallback_model: str) -> LlmResponse:
    try:
        data = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError):
        raise LlmResponseError("la respuesta del proveedor no es JSON válido") from None
    if not isinstance(data, dict):
        raise LlmResponseError("la respuesta del proveedor no es un objeto")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LlmResponseError("la respuesta no contiene 'choices'")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise LlmResponseError("la respuesta no contiene texto en 'message.content'")
    finish = choices[0].get("finish_reason")
    raw_usage = data.get("usage")
    usage: dict[str, object] = raw_usage if isinstance(raw_usage, dict) else {}
    model = data.get("model")
    return LlmResponse(
        text=message["content"],
        usage=TokenUsage(
            prompt=_non_negative_int(usage.get("prompt_tokens")),
            completion=_non_negative_int(usage.get("completion_tokens")),
        ),
        model=model if isinstance(model, str) else fallback_model,
        finish_reason=finish if isinstance(finish, str) else None,
        http_status=200,
    )


def _message_json(message: ChatMessage) -> dict[str, str]:
    return {"role": message.role, "content": message.content}


class NvidiaChatClient:
    """Implementación de `LlmClient` para `POST {base_url}/chat/completions`."""

    def __init__(
        self,
        base_url: str,
        api_key: Secret,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._url = f"{_check_base_url(base_url)}/chat/completions"
        self._key = api_key
        self._now = now
        self._opener = urllib.request.build_opener(_NoRedirect())

    @property
    def provider(self) -> str:
        return PROVIDER

    def __repr__(self) -> str:
        return f"NvidiaChatClient(url={self._url!r}, api_key=***)"

    def complete(self, request: LlmRequest) -> LlmResponse:
        body = json.dumps(
            {
                "model": request.model,
                "messages": [_message_json(m) for m in request.messages],
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "stream": False,
            }
        ).encode("utf-8")
        http_request = urllib.request.Request(  # noqa: S310 - URL validada en el constructor
            self._url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        # Cabecera no redirigible: aunque hubiera una redirección, la clave no viajaría.
        http_request.add_unredirected_header("Authorization", f"Bearer {self._key.reveal()}")
        raw = self._send(http_request, request.timeout_s)
        return _parse_envelope(raw, request.model)

    def _send(self, http_request: urllib.request.Request, timeout_s: float) -> bytes:
        started = time.monotonic()
        try:
            with self._opener.open(http_request, timeout=timeout_s) as response:
                return self._read_ok(response, started, timeout_s)
        except urllib.error.HTTPError as error:
            error.close()  # el cuerpo de error se descarta: puede reproducir la petición
            raise self._http_error(error.code, error.headers) from None
        except urllib.error.URLError as error:
            raise self._connection_error(error.reason) from None
        except TimeoutError:
            raise LlmTimeoutError(f"{PROVIDER}: se agotó el plazo de {timeout_s} s") from None
        except OSError as error:
            # Conexión cortada tras enviar: no es seguro repetir la petición.
            raise LlmConnectionError(
                f"{PROVIDER}: conexión interrumpida ({type(error).__name__})", retryable=False
            ) from None

    def _read_ok(self, response: HTTPResponse, started: float, timeout_s: float) -> bytes:
        if response.status != 200:
            raise LlmProviderError(
                f"{PROVIDER} respondió {response.status}", status=response.status, retryable=False
            )
        chunks: list[bytes] = []
        size = 0
        while chunk := response.read(_CHUNK_BYTES):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise LlmResponseError(f"la respuesta supera el tamaño de {MAX_RESPONSE_BYTES}")
            if time.monotonic() - started > timeout_s:
                raise TimeoutError
            chunks.append(chunk)
        if time.monotonic() - started > timeout_s:
            raise TimeoutError
        return b"".join(chunks)

    def _http_error(self, status: int, headers: Any) -> LlmProviderError | LlmRateLimitError:
        if status == 429:
            retry_after = parse_retry_after(headers.get("Retry-After"), self._now())
            return LlmRateLimitError(f"{PROVIDER}: límite de tasa (429)", retry_after_s=retry_after)
        return LlmProviderError(
            f"{PROVIDER} respondió {status}", status=status, retryable=status in _RETRYABLE_STATUSES
        )

    @staticmethod
    def _connection_error(reason: object) -> LlmTimeoutError | LlmConnectionError:
        if isinstance(reason, TimeoutError):
            return LlmTimeoutError(f"{PROVIDER}: se agotó el plazo al conectar")
        # Solo un rechazo de conexión garantiza que la petición no llegó al proveedor.
        refused = isinstance(reason, ConnectionRefusedError)
        name = type(reason).__name__
        return LlmConnectionError(f"{PROVIDER}: no se pudo conectar ({name})", retryable=refused)
