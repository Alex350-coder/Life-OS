"""F2-T01: cliente NVIDIA (compatible con OpenAI) contra un servidor local programado."""

import json
import socket
from datetime import UTC, datetime

import pytest

from lifeos.adapters.nvidia_llm import MAX_RESPONSE_BYTES, NvidiaChatClient, parse_retry_after
from lifeos.adapters.secrets import Secret
from lifeos.ports.llm import (
    ChatMessage,
    LlmConnectionError,
    LlmError,
    LlmProviderError,
    LlmRateLimitError,
    LlmRequest,
    LlmResponseError,
    LlmTimeoutError,
)
from tests.stub_http import Reply, StubServer

# Se compone en dos partes para que el archivo no contenga un patrón de clave real.
FAKE_KEY = "nvapi" + "-TESTKEY-0123456789abcdefXYZ"
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

pytestmark = [pytest.mark.integration]


def _client(stub: StubServer) -> NvidiaChatClient:
    return NvidiaChatClient(stub.base_url, Secret(FAKE_KEY), now=lambda: NOW)


def _request(timeout_s: float = 5.0) -> LlmRequest:
    return LlmRequest(
        model="proveedor/modelo",
        messages=(ChatMessage("system", "Responde en JSON."), ChatMessage("user", "hola")),
        timeout_s=timeout_s,
        max_tokens=64,
    )


def _completion(**overrides: object) -> bytes:
    body: dict[str, object] = {
        "id": "chatcmpl-1",
        "model": "proveedor/modelo",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": '{"ok": true}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 21, "completion_tokens": 4, "total_tokens": 25},
    }
    body.update(overrides)
    return json.dumps(body).encode()


# --- Llamada correcta ---------------------------------------------------------------------


def test_successful_call_maps_text_usage_and_sends_expected_request(
    stub_server: StubServer,
) -> None:
    stub_server.replies.append(Reply(body=_completion()))
    response = _client(stub_server).complete(_request())

    assert response.text == '{"ok": true}'
    assert (response.usage.prompt, response.usage.completion) == (21, 4)
    assert response.finish_reason == "stop"
    assert response.http_status == 200

    received = stub_server.received[0]
    assert received.path == "/v1/chat/completions"
    assert received.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    sent = json.loads(received.body)
    assert sent["model"] == "proveedor/modelo"
    assert sent["max_tokens"] == 64
    assert sent["stream"] is False
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert "response_format" not in sent


def test_missing_usage_is_unknown_not_zero(stub_server: StubServer) -> None:
    body = json.loads(_completion())
    del body["usage"]
    stub_server.replies.append(Reply(body=json.dumps(body).encode()))
    usage = _client(stub_server).complete(_request()).usage
    assert usage.prompt is None
    assert usage.completion is None


@pytest.mark.parametrize("bad", [-1, "12", True, 1.5, None])
def test_invalid_token_counts_are_unknown(stub_server: StubServer, bad: object) -> None:
    stub_server.replies.append(
        Reply(body=_completion(usage={"prompt_tokens": bad, "completion_tokens": bad}))
    )
    usage = _client(stub_server).complete(_request()).usage
    assert (usage.prompt, usage.completion) == (None, None)


def test_reasoning_content_is_ignored(stub_server: StubServer) -> None:
    choice = {
        "index": 0,
        "message": {"role": "assistant", "content": "final", "reasoning_content": "secreto"},
        "finish_reason": "stop",
    }
    stub_server.replies.append(Reply(body=_completion(choices=[choice])))
    assert _client(stub_server).complete(_request()).text == "final"


# --- Errores del proveedor ----------------------------------------------------------------


def test_timeout_raises_timeout_error(stub_server: StubServer) -> None:
    stub_server.replies.append(Reply(body=_completion(), delay_s=1.0))
    with pytest.raises(LlmTimeoutError):
        _client(stub_server).complete(_request(timeout_s=0.2))


def test_slow_trickling_body_hits_total_deadline(stub_server: StubServer) -> None:
    # Cada fragmento llega antes del timeout por operación, pero el total lo supera.
    stub_server.replies.append(Reply(body=_completion(), chunks=8, chunk_delay_s=0.15))
    with pytest.raises(LlmTimeoutError):
        _client(stub_server).complete(_request(timeout_s=0.4))


def test_rate_limit_with_retry_after_seconds(stub_server: StubServer) -> None:
    stub_server.replies.append(
        Reply(
            status=429,
            body=b'{"status":429,"title":"Too Many Requests"}',
            headers=(("Retry-After", "2"),),
        )
    )
    with pytest.raises(LlmRateLimitError) as info:
        _client(stub_server).complete(_request())
    assert info.value.retry_after_s == 2.0


def test_rate_limit_without_retry_after(stub_server: StubServer) -> None:
    stub_server.replies.append(Reply(status=429, body=b"{}"))
    with pytest.raises(LlmRateLimitError) as info:
        _client(stub_server).complete(_request())
    assert info.value.retry_after_s is None


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(500, False), (502, True), (503, True), (504, True), (400, False), (422, False)],
)
def test_http_errors_map_to_provider_error(
    stub_server: StubServer, status: int, retryable: bool
) -> None:
    stub_server.replies.append(Reply(status=status, body=b'{"detail":"x"}'))
    with pytest.raises(LlmProviderError) as info:
        _client(stub_server).complete(_request())
    assert info.value.status == status
    assert info.value.retryable is retryable


def test_pending_202_is_not_polled(stub_server: StubServer) -> None:
    stub_server.replies.append(Reply(status=202, body=b"{}"))
    with pytest.raises(LlmProviderError) as info:
        _client(stub_server).complete(_request())
    assert info.value.status == 202
    assert len(stub_server.received) == 1


def test_unauthorized_error_never_contains_the_key(stub_server: StubServer) -> None:
    echo = json.dumps({"detail": f"bad key {FAKE_KEY}"}).encode()
    stub_server.replies.append(Reply(status=401, body=echo))
    with pytest.raises(LlmProviderError) as info:
        _client(stub_server).complete(_request())
    assert FAKE_KEY not in str(info.value)
    assert FAKE_KEY not in repr(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_redirects_are_not_followed(stub_server: StubServer) -> None:
    stub_server.replies.append(Reply(status=302, headers=(("Location", "/robar-clave"),)))
    with pytest.raises(LlmProviderError) as info:
        _client(stub_server).complete(_request())
    assert info.value.status == 302
    assert [r.path for r in stub_server.received] == ["/v1/chat/completions"]


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_connection_refused_is_a_retryable_connection_error() -> None:
    # En Windows el rechazo tarda ~2 s (reintentos de SYN): el plazo debe superarlo.
    url = f"http://127.0.0.1:{_closed_port()}/v1"
    client = NvidiaChatClient(url, Secret(FAKE_KEY), now=lambda: NOW)
    with pytest.raises(LlmConnectionError) as info:
        client.complete(_request(timeout_s=10))
    assert info.value.retryable is True


@pytest.mark.parametrize(
    ("reason", "expected", "retryable"),
    [
        (ConnectionRefusedError(), LlmConnectionError, True),
        (socket.gaierror(), LlmConnectionError, False),
        (TimeoutError(), LlmTimeoutError, False),
    ],
)
def test_connection_failures_are_classified(
    reason: OSError, expected: type[LlmError], retryable: bool
) -> None:
    error = NvidiaChatClient._connection_error(reason)
    assert type(error) is expected
    assert error.retryable is retryable


# --- Respuestas malformadas ---------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"no es json",
        b"[]",
        b'{"choices": []}',
        b'{"choices": [{"message": {"content": null}}]}',
        b'{"choices": [{"message": {"content": 5}}]}',
        b'{"choices": [{"message": "x"}]}',
        b'{"choices": [{"message": {"content": "x"}}], "usage": NaN}',
    ],
)
def test_malformed_envelope_raises_response_error(stub_server: StubServer, body: bytes) -> None:
    stub_server.replies.append(Reply(body=body))
    with pytest.raises(LlmResponseError):
        _client(stub_server).complete(_request())


def test_oversized_response_is_rejected(stub_server: StubServer) -> None:
    stub_server.replies.append(Reply(body=b"x" * (MAX_RESPONSE_BYTES + 10)))
    with pytest.raises(LlmResponseError, match="tamaño"):
        _client(stub_server).complete(_request())


# --- Construcción segura -------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["http://integrate.api.nvidia.com/v1", "ftp://x/v1", "http://localhost:80/v1", "file:///x"],
)
def test_only_https_or_loopback_ip_is_allowed(url: str) -> None:
    with pytest.raises(ValueError, match="https"):
        NvidiaChatClient(url, Secret(FAKE_KEY))


def test_client_repr_hides_the_key() -> None:
    client = NvidiaChatClient("https://integrate.api.nvidia.com/v1", Secret(FAKE_KEY))
    assert FAKE_KEY not in repr(client)
    assert client.provider == "nvidia"


# --- Retry-After ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2", 2.0),
        (" 10 ", 10.0),
        ("0", 0.0),
        ("Tue, 27 Oct 2026 12:00:30 GMT", 30.0),
        ("Tue, 27 Oct 2026 11:00:00 GMT", 0.0),  # fecha pasada: reintentar ya
        (None, None),
        ("", None),
        ("-5", None),
        ("1.5", None),
        ("mañana", None),
        ("٣", None),  # dígito no ASCII
        ("9" * 40, None),
    ],
)
def test_parse_retry_after(value: str | None, expected: float | None) -> None:
    assert parse_retry_after(value, NOW) == expected
