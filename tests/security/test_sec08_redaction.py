"""SEC-08 de extremo a extremo: cliente real contra un servidor local, logs JSONL en disco.

Tras una llamada correcta, un 401 que repite la clave y un timeout, la clave no aparece en
ningún archivo de log, en los mensajes registrados ni en las excepciones.
"""

import json
import logging
from pathlib import Path

import pytest

from lifeos.adapters.jsonl_logs import JsonlUsageLog
from lifeos.adapters.nvidia_llm import NvidiaChatClient
from lifeos.adapters.secrets import Secret
from lifeos.adapters.system_clock import SystemClock
from lifeos.adapters.yaml_config import parse_catalog
from lifeos.application.llm_calls import CallContext, ModelGateway, RetryPolicy
from lifeos.ports.llm import ChatMessage, LlmError
from tests import config_samples as samples
from tests.stub_http import Reply, StubServer

KEY = "nvapi" + "-TESTKEY-0123456789abcdefXYZ"  # en dos partes: sin patrón real en el archivo
CTX = CallContext(agent_id="coach", ritual="sec08", profile="ligero", correlation_id="c-sec08")

pytestmark = [pytest.mark.security, pytest.mark.integration]


def _ok_body() -> bytes:
    return json.dumps(
        {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }
    ).encode()


def test_api_key_never_reaches_logs_or_errors(
    stub_server: StubServer, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    stub_server.replies.extend(
        [
            Reply(body=_ok_body()),
            Reply(status=401, body=json.dumps({"detail": f"invalid {KEY}"}).encode()),
            Reply(body=_ok_body(), delay_s=1.5),
        ]
    )
    catalog_data = samples.catalog()
    catalog_data["perfiles"]["ligero"]["timeout_s"] = 0.3
    gateway = ModelGateway(
        {"nvidia": NvidiaChatClient(stub_server.base_url, Secret(KEY))},
        parse_catalog(samples.dump(catalog_data).encode()),
        SystemClock(),
        JsonlUsageLog(tmp_path, secrets=(KEY,)),
        retry=RetryPolicy(max_attempts=1),
    )
    errors: list[LlmError] = []
    for _ in range(3):
        try:
            gateway.complete(CTX, (ChatMessage("user", "hola"),))
        except LlmError as error:
            errors.append(error)

    assert [type(e).__name__ for e in errors] == ["LlmProviderError", "LlmTimeoutError"]
    logged = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.jsonl"))
    assert [json.loads(line)["outcome"] for line in logged.splitlines()] == [
        "ok",
        "error_proveedor",
        "timeout",
    ]
    assert KEY not in logged
    assert "Bearer" not in logged
    assert KEY not in caplog.text
    for raised in errors:
        assert KEY not in str(raised)
        assert KEY not in repr(raised)
        assert raised.__cause__ is None
    # La clave sí viajó al proveedor: el control está en lo que se registra, no en no enviarla.
    assert stub_server.received[0].headers["Authorization"] == f"Bearer {KEY}"
