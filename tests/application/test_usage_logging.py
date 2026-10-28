"""F2-T04: cada intento de llamada a un modelo queda registrado; tokens y costo no se inventan."""

from decimal import Decimal
from typing import Any

import pytest

from lifeos.adapters.yaml_config import parse_catalog
from lifeos.application.llm_calls import CallContext, ModelGateway, estimate_cost
from lifeos.ports.config import ModelCatalog
from lifeos.ports.llm import (
    ChatMessage,
    LlmProviderError,
    LlmRateLimitError,
    LlmTimeoutError,
    TokenUsage,
)
from lifeos.ports.logs import CostStatus, LogWriteError
from tests import config_samples as samples
from tests.fakes import FakeClock, FakeLlmClient, MemoryUsageLog, ok_response

pytestmark = [pytest.mark.unit]

CTX = CallContext(agent_id="coach", ritual="checkin", profile="ligero", correlation_id="c-7")
MESSAGES = (ChatMessage("user", "hola"),)
MODEL = "proveedor/modelo-ligero"


def _catalog(with_tariff: bool) -> ModelCatalog:
    data: dict[str, Any] = samples.catalog()
    if with_tariff:
        data["tarifas"] = {
            "fecha_tarifa": "2026-10-27",
            "modelos": {MODEL: {"entrada_usd_por_millon": "0.10", "salida_usd_por_millon": "0.40"}},
        }
    return parse_catalog(samples.dump(data).encode())


def _run(script: list[object], *, with_tariff: bool = False, log: MemoryUsageLog | None = None):  # type: ignore[no-untyped-def]
    clock = FakeClock()
    client = FakeLlmClient(script=list(script), clock=clock, latency_s=0.25)  # type: ignore[arg-type]
    usage_log = log or MemoryUsageLog()
    gateway = ModelGateway(
        {"nvidia": client}, _catalog(with_tariff), clock, usage_log, jitter=lambda: 0.0
    )
    return gateway, usage_log


def test_successful_call_logs_one_complete_record() -> None:
    gateway, log = _run([ok_response(prompt=21, completion=4)])
    gateway.complete(CTX, MESSAGES)
    [record] = log.records
    assert record.correlation_id == "c-7"
    assert (record.provider, record.model, record.profile) == ("nvidia", MODEL, "ligero")
    assert (record.agent, record.ritual, record.attempt) == ("coach", "checkin", 1)
    assert record.latency_ms == 250
    assert (record.tokens_prompt, record.tokens_completion) == (21, 4)
    assert record.outcome == "ok"
    assert record.http_status == 200
    assert record.ts.utcoffset() is not None


def test_cost_is_unknown_without_tariff_never_zero() -> None:
    gateway, log = _run([ok_response()])
    gateway.complete(CTX, MESSAGES)
    assert log.records[0].cost_usd is None
    assert log.records[0].cost_status is CostStatus.DESCONOCIDO


def test_cost_is_estimated_exactly_with_dated_tariff() -> None:
    gateway, log = _run([ok_response(prompt=1_000, completion=500)], with_tariff=True)
    gateway.complete(CTX, MESSAGES)
    assert log.records[0].cost_usd == Decimal("0.0003")  # 1000*0.10/1e6 + 500*0.40/1e6
    assert log.records[0].cost_status is CostStatus.ESTIMADO


def test_missing_tokens_make_cost_unknown_even_with_tariff() -> None:
    gateway, log = _run([ok_response(prompt=None, completion=None)], with_tariff=True)
    gateway.complete(CTX, MESSAGES)
    record = log.records[0]
    assert (record.tokens_prompt, record.tokens_completion) == (None, None)
    assert record.cost_usd is None
    assert record.cost_status is CostStatus.DESCONOCIDO


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (TokenUsage(10, None), (None, CostStatus.DESCONOCIDO)),
        (TokenUsage(None, 10), (None, CostStatus.DESCONOCIDO)),
        (TokenUsage(0, 0), (Decimal(0), CostStatus.ESTIMADO)),
    ],
)
def test_estimate_cost_requires_both_counts(
    usage: TokenUsage, expected: tuple[Decimal | None, CostStatus]
) -> None:
    assert estimate_cost(usage, MODEL, _catalog(with_tariff=True)) == expected


def test_each_retry_attempt_is_logged_with_its_outcome() -> None:
    gateway, log = _run(
        [
            LlmRateLimitError("429", retry_after_s=1.0),
            LlmProviderError("503", status=503, retryable=True),
            ok_response(),
        ]
    )
    gateway.complete(CTX, MESSAGES)
    assert [(r.attempt, r.outcome, r.http_status) for r in log.records] == [
        (1, "limite_tasa", 429),
        (2, "error_proveedor", 503),
        (3, "ok", 200),
    ]
    assert all(r.correlation_id == "c-7" for r in log.records)
    assert all(r.tokens_prompt is None for r in log.records[:2])


def test_timeout_is_logged_once_and_raised() -> None:
    gateway, log = _run([LlmTimeoutError("tarde")])
    with pytest.raises(LlmTimeoutError):
        gateway.complete(CTX, MESSAGES)
    assert [(r.outcome, r.http_status) for r in log.records] == [("timeout", None)]


def test_usage_log_failure_is_not_hidden() -> None:
    gateway, _ = _run([ok_response()], log=MemoryUsageLog(fail=True))
    with pytest.raises(LogWriteError):
        gateway.complete(CTX, MESSAGES)
