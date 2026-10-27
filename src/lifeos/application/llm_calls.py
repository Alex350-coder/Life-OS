"""Pasarela de modelos: perfil → proveedor/modelo, reintentos seguros y límites de tasa.

Política de reintentos (ADR-0007): se reintenta solo lo que no pudo procesarse en el
proveedor (429, 502/503/504 y conexión rechazada), con un máximo de intentos y de espera
acumulada. Un timeout nunca se reintenta: la petición pudo ejecutarse y facturarse.
"""

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from lifeos.application.tasks import ApplicationError
from lifeos.domain.errors import ValidationError
from lifeos.ports.clock import Clock
from lifeos.ports.config import ModelCatalog, Profile
from lifeos.ports.llm import (
    ChatMessage,
    LlmClient,
    LlmError,
    LlmProviderError,
    LlmRateLimitError,
    LlmRequest,
    LlmResponse,
    ModelOutputError,
    OutputSchema,
    TokenUsage,
)
from lifeos.ports.logs import CostStatus, UsageLog, UsageRecord

MAX_PROMPT_CHARS = 200_000
_TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)  # las tarifas se expresan por millón de tokens
_TOO_MANY_REQUESTS = 429
_OK = "ok"
_TRUNCATED = "length"  # finish_reason cuando se alcanzó max_tokens
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


def estimate_cost(
    usage: TokenUsage, model: str, catalog: ModelCatalog
) -> tuple[Decimal | None, CostStatus]:
    """Costo exacto solo con ambos conteos de tokens y una tarifa fechada; si no, desconocido."""
    tariff = catalog.tariffs.get(model)
    if tariff is None or catalog.tariff_date is None:
        return None, CostStatus.DESCONOCIDO
    if usage.prompt is None or usage.completion is None:
        return None, CostStatus.DESCONOCIDO
    cost = (
        usage.prompt * tariff.input_usd_per_million
        + usage.completion * tariff.output_usd_per_million
    ) / _TOKENS_PER_PRICE_UNIT
    return cost, CostStatus.ESTIMADO


def _http_status(error: LlmError) -> int | None:
    if isinstance(error, LlmRateLimitError):
        return _TOO_MANY_REQUESTS
    if isinstance(error, LlmProviderError):
        return error.status
    return None


class ModelGateway:
    """Única puerta hacia los modelos: enruta por perfil, reintenta y registra cada intento."""

    def __init__(
        self,
        clients: Mapping[str, LlmClient],
        catalog: ModelCatalog,
        clock: Clock,
        usage_log: UsageLog,
        *,
        retry: RetryPolicy | None = None,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._clients = dict(clients)
        self._catalog = catalog
        self._clock = clock
        self._usage_log = usage_log
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
        """Devuelve la respuesta del modelo; cada intento queda en el log de uso."""
        return self._run(ctx, messages, idempotent=idempotent, accept=lambda _response: None)

    def complete_structured[T](
        self,
        ctx: CallContext,
        messages: tuple[ChatMessage, ...],
        schema: OutputSchema[T],
        *,
        idempotent: bool = True,
    ) -> T:
        """Devuelve solo un objeto validado por `schema` (SEC-07). Una salida truncada o
        malformada se registra como `salida_invalida` y se rechaza; nunca se devuelve parcial."""
        parsed: list[T] = []

        def accept(response: LlmResponse) -> str | None:
            if response.finish_reason == _TRUNCATED:
                return ModelOutputError.outcome
            try:
                parsed.append(schema.parse(response.text))
            except ModelOutputError:
                return ModelOutputError.outcome
            return None

        self._run(ctx, messages, idempotent=idempotent, accept=accept)
        if not parsed:
            raise ModelOutputError(f"salida no válida o truncada para {schema.id}")
        return parsed[0]

    def _run(
        self,
        ctx: CallContext,
        messages: tuple[ChatMessage, ...],
        *,
        idempotent: bool,
        accept: Callable[[LlmResponse], str | None],
    ) -> LlmResponse:
        """`accept` devuelve el resultado a registrar si la respuesta no es utilizable."""
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
            started = self._clock.monotonic()
            try:
                response = client.complete(request)
            except LlmError as error:
                self._record(
                    ctx, profile, attempt, started, error.outcome, None, _http_status(error)
                )
                delay = self._delay(error, attempt, waited, idempotent=idempotent)
                if delay is None:
                    raise
                self._clock.sleep(delay)
                waited += delay
                attempt += 1
                continue
            rejected = accept(response)
            outcome = rejected or _OK
            self._record(ctx, profile, attempt, started, outcome, response, response.http_status)
            return response

    def _record(
        self,
        ctx: CallContext,
        profile: Profile,
        attempt: int,
        started: float,
        outcome: str,
        response: LlmResponse | None,
        http_status: int | None,
    ) -> None:
        usage = response.usage if response is not None else TokenUsage(None, None)
        model = response.model if response is not None else profile.model
        cost, status = estimate_cost(usage, model, self._catalog)
        latency_ms = round((self._clock.monotonic() - started) * 1000)
        self._usage_log.append(
            UsageRecord(
                ts=self._clock.now(),
                correlation_id=ctx.correlation_id,
                provider=profile.provider,
                model=model,
                profile=profile.name,
                agent=ctx.agent_id,
                ritual=ctx.ritual,
                attempt=attempt,
                latency_ms=latency_ms,
                tokens_prompt=usage.prompt,
                tokens_completion=usage.completion,
                cost_usd=cost,
                cost_status=status,
                outcome=outcome,
                http_status=http_status,
            )
        )

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
