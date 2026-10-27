"""Pasarela de modelos: perfil → proveedor/modelo, reintentos seguros y límites de tasa.

Política de reintentos (ADR-0007): se reintenta solo lo que no pudo procesarse en el
proveedor (429, 502/503/504 y conexión rechazada), con un máximo de intentos y de espera
acumulada. Un timeout nunca se reintenta: la petición pudo ejecutarse y facturarse.
"""

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from lifeos.application.tasks import ApplicationError
from lifeos.domain.errors import ValidationError
from lifeos.ports.clock import Clock
from lifeos.ports.config import ModelCatalog, Profile
from lifeos.ports.llm import (
    ChatMessage,
    LlmClient,
    LlmError,
    LlmRateLimitError,
    LlmRequest,
    LlmResponse,
)

MAX_PROMPT_CHARS = 200_000
JITTER_FRACTION = 0.5  # el backoff crece hasta un 50 % para no sincronizar reintentos


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    max_total_wait_s: float = 30.0
    base_backoff_s: float = 1.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts debe ser al menos 1")
        if self.max_total_wait_s < 0 or self.base_backoff_s < 0:
            raise ValueError("las esperas no pueden ser negativas")


@dataclass(frozen=True, slots=True)
class CallContext:
    agent_id: str
    ritual: str
    profile: str
    correlation_id: str


def _validate_messages(messages: tuple[ChatMessage, ...]) -> None:
    if not messages:
        raise ValidationError("mensajes", len(messages), "se necesita al menos un mensaje")
    total = sum(len(message.content) for message in messages)
    if total > MAX_PROMPT_CHARS:
        raise ValidationError("mensajes", total, f"máximo {MAX_PROMPT_CHARS} caracteres")


class ModelGateway:
    def __init__(
        self,
        clients: Mapping[str, LlmClient],
        catalog: ModelCatalog,
        clock: Clock,
        *,
        retry: RetryPolicy | None = None,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._clients = dict(clients)
        self._catalog = catalog
        self._clock = clock
        self._retry = retry or RetryPolicy()
        self._jitter = jitter

    def _route(self, profile_name: str) -> tuple[Profile, LlmClient]:
        profile = self._catalog.profiles.get(profile_name)
        if profile is None:
            raise ApplicationError(f"perfil de modelo desconocido: {profile_name}")
        client = self._clients.get(profile.provider)
        if client is None:
            raise ApplicationError(f"no hay cliente configurado para {profile.provider}")
        return profile, client

    def complete(
        self,
        ctx: CallContext,
        messages: tuple[ChatMessage, ...],
        *,
        idempotent: bool = True,
    ) -> LlmResponse:
        _validate_messages(messages)
        profile, client = self._route(ctx.profile)
        request = LlmRequest(
            model=profile.model,
            messages=messages,
            timeout_s=profile.timeout_s,
            max_tokens=profile.max_tokens,
        )
        waited = 0.0
        attempt = 1
        while True:
            try:
                return client.complete(request)
            except LlmError as error:
                delay = self._delay(error, attempt, waited, idempotent=idempotent)
                if delay is None:
                    raise
                self._clock.sleep(delay)
                waited += delay
                attempt += 1

    def _delay(
        self, error: LlmError, attempt: int, waited: float, *, idempotent: bool
    ) -> float | None:
        """Espera antes del siguiente intento, o `None` si no debe reintentarse."""
        if not idempotent or not error.retryable or attempt >= self._retry.max_attempts:
            return None
        if isinstance(error, LlmRateLimitError) and error.retry_after_s is not None:
            delay = error.retry_after_s
        else:
            backoff = self._retry.base_backoff_s * 2 ** (attempt - 1)
            delay = backoff * (1 + JITTER_FRACTION * self._jitter())
        if waited + delay > self._retry.max_total_wait_s:
            return None
        return delay
