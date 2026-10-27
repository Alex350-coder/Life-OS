"""Puerto de modelos de lenguaje (ADR-0007). Los casos de uso no conocen ningún proveedor."""

from dataclasses import dataclass
from typing import Literal, Protocol

from lifeos.domain.errors import LifeOSError

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class LlmRequest:
    model: str
    messages: tuple[ChatMessage, ...]
    timeout_s: float
    max_tokens: int
    temperature: float = 0.2


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Tokens informados por el proveedor; `None` significa desconocido, nunca cero."""

    prompt: int | None
    completion: int | None


@dataclass(frozen=True, slots=True)
class LlmResponse:
    text: str
    usage: TokenUsage
    model: str
    finish_reason: str | None
    http_status: int


class LlmClient(Protocol):
    @property
    def provider(self) -> str: ...

    def complete(self, request: LlmRequest) -> LlmResponse: ...


# --- Errores: el atributo `outcome` es la categoría que se registra en el log de uso ------------


class LlmError(LifeOSError):
    """Fallo técnico al llamar a un modelo. Los mensajes nunca incluyen cuerpos ni claves."""

    outcome = "error_proveedor"
    retryable = False


class LlmTimeoutError(LlmError):
    """Se agotó el plazo. No se reintenta: la petición pudo procesarse (y facturarse)."""

    outcome = "timeout"


class LlmRateLimitError(LlmError):
    outcome = "limite_tasa"
    retryable = True

    def __init__(self, message: str, *, retry_after_s: float | None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class LlmProviderError(LlmError):
    def __init__(self, message: str, *, status: int | None, retryable: bool) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class LlmConnectionError(LlmError):
    """No se pudo conectar. Solo es reintentable si la petición no llegó a enviarse."""

    outcome = "error_conexion"

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class LlmResponseError(LlmError):
    """El proveedor respondió con un sobre que no cumple el formato esperado."""

    outcome = "respuesta_malformada"
