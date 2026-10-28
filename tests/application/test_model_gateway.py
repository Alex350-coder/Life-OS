"""F2-T01: pasarela de modelos — perfiles, reintentos seguros y límites de tasa."""

import pytest

from lifeos.adapters.yaml_config import parse_catalog
from lifeos.application.llm_calls import CallContext, ModelGateway, RetryPolicy
from lifeos.application.tasks import ApplicationError
from lifeos.domain.errors import ValidationError
from lifeos.ports.llm import (
    ChatMessage,
    LlmConnectionError,
    LlmError,
    LlmProviderError,
    LlmRateLimitError,
    LlmResponseError,
    LlmTimeoutError,
)
from tests import config_samples as samples
from tests.fakes import FakeClock, FakeLlmClient, MemoryUsageLog, ok_response

pytestmark = [pytest.mark.unit]

CTX = CallContext(agent_id="coach", ritual="prueba", profile="ligero", correlation_id="c-1")
MESSAGES = (ChatMessage("user", "hola"),)


def _gateway(
    script: list[object], clock: FakeClock | None = None, retry: RetryPolicy | None = None
) -> tuple[ModelGateway, FakeLlmClient, FakeClock]:
    clock = clock or FakeClock()
    client = FakeLlmClient(script=list(script), clock=clock)  # type: ignore[arg-type]
    catalog = parse_catalog(samples.dump(samples.catalog()).encode())
    gateway = ModelGateway(
        {"nvidia": client},
        catalog,
        clock,
        MemoryUsageLog(),
        retry=retry or RetryPolicy(),
        jitter=lambda: 0.0,
    )
    return gateway, client, clock


def test_profile_selects_model_timeout_and_max_tokens() -> None:
    gateway, client, _ = _gateway([ok_response()])
    response = gateway.complete(CTX, MESSAGES)
    assert response.text == '{"ok": true}'
    request = client.requests[0]
    assert request.model == "proveedor/modelo-ligero"
    assert request.timeout_s == 30
    assert request.max_tokens == 1024
    assert request.messages == MESSAGES


def test_unknown_profile_or_provider_is_an_application_error() -> None:
    gateway, _, _ = _gateway([])
    with pytest.raises(ApplicationError, match="perfil"):
        gateway.complete(CallContext("coach", "r", "enorme", "c"), MESSAGES)


def test_messages_are_validated() -> None:
    gateway, _, _ = _gateway([])
    with pytest.raises(ValidationError):
        gateway.complete(CTX, ())
    with pytest.raises(ValidationError):
        gateway.complete(CTX, (ChatMessage("user", "x" * 200_001),))


# --- Política de reintentos ---------------------------------------------------------------


def test_timeout_is_never_retried() -> None:
    gateway, client, clock = _gateway([LlmTimeoutError("tarde"), ok_response()])
    with pytest.raises(LlmTimeoutError):
        gateway.complete(CTX, MESSAGES)
    assert len(client.requests) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("status", [502, 503, 504])
def test_transient_server_errors_are_retried_with_backoff(status: int) -> None:
    error = LlmProviderError("caído", status=status, retryable=True)
    gateway, client, clock = _gateway([error, error, ok_response()])
    assert gateway.complete(CTX, MESSAGES).text == '{"ok": true}'
    assert len(client.requests) == 3
    assert clock.sleeps == [1.0, 2.0]


def test_retries_stop_at_max_attempts() -> None:
    error = LlmProviderError("caído", status=503, retryable=True)
    gateway, client, _ = _gateway([error, error, error, ok_response()])
    with pytest.raises(LlmProviderError):
        gateway.complete(CTX, MESSAGES)
    assert len(client.requests) == 3


@pytest.mark.parametrize(
    "error",
    [
        LlmProviderError("interno", status=500, retryable=False),
        LlmProviderError("no autorizado", status=401, retryable=False),
        LlmResponseError("malformada"),
        LlmConnectionError("dns", retryable=False),
    ],
)
def test_non_retryable_errors_fail_immediately(error: LlmError) -> None:
    gateway, client, _ = _gateway([error, ok_response()])
    with pytest.raises(type(error)):
        gateway.complete(CTX, MESSAGES)
    assert len(client.requests) == 1


def test_connection_refused_is_retried() -> None:
    gateway, client, _ = _gateway([LlmConnectionError("rechazada", retryable=True), ok_response()])
    gateway.complete(CTX, MESSAGES)
    assert len(client.requests) == 2


def test_rate_limit_honours_retry_after() -> None:
    gateway, client, clock = _gateway([LlmRateLimitError("429", retry_after_s=2.0), ok_response()])
    gateway.complete(CTX, MESSAGES)
    assert clock.sleeps == [2.0]
    assert len(client.requests) == 2


def test_rate_limit_with_excessive_retry_after_fails_fast() -> None:
    gateway, client, clock = _gateway([LlmRateLimitError("429", retry_after_s=3600.0)])
    with pytest.raises(LlmRateLimitError):
        gateway.complete(CTX, MESSAGES)
    assert clock.sleeps == []
    assert len(client.requests) == 1


def test_rate_limit_without_retry_after_uses_backoff() -> None:
    gateway, _, clock = _gateway([LlmRateLimitError("429", retry_after_s=None), ok_response()])
    gateway.complete(CTX, MESSAGES)
    assert clock.sleeps == [1.0]


def test_total_wait_is_bounded() -> None:
    retry = RetryPolicy(max_attempts=5, max_total_wait_s=5.0, base_backoff_s=2.0)
    error = LlmProviderError("caído", status=503, retryable=True)
    gateway, client, clock = _gateway([error] * 5, retry=retry)
    with pytest.raises(LlmProviderError):
        gateway.complete(CTX, MESSAGES)
    assert sum(clock.sleeps) <= 5.0
    assert len(client.requests) == 2  # 2 s de espera; la siguiente (4 s) superaría el límite


def test_non_idempotent_calls_are_never_retried() -> None:
    error = LlmProviderError("caído", status=503, retryable=True)
    gateway, client, _ = _gateway([error, ok_response()])
    with pytest.raises(LlmProviderError):
        gateway.complete(CTX, MESSAGES, idempotent=False)
    assert len(client.requests) == 1


def test_jitter_only_lengthens_backoff_within_bounds() -> None:
    catalog = parse_catalog(samples.dump(samples.catalog()).encode())
    clock = FakeClock()
    error = LlmProviderError("caído", status=503, retryable=True)
    client = FakeLlmClient(script=[error, ok_response()], clock=clock)
    gateway = ModelGateway(
        {"nvidia": client}, catalog, clock, MemoryUsageLog(), jitter=lambda: 0.999
    )
    gateway.complete(CTX, MESSAGES)
    assert 1.0 <= clock.sleeps[0] <= 1.5


def test_retry_policy_rejects_nonsense_values() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(max_total_wait_s=-1)
