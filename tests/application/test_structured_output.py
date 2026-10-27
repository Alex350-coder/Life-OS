"""SEC-07 de extremo a extremo: una salida malformada no se persiste ni llega a las herramientas."""

from datetime import time

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.schemas import SCHEMAS
from lifeos.adapters.yaml_config import parse_catalog
from lifeos.application.llm_calls import CallContext, ModelGateway
from lifeos.application.tools import ActorContext, ToolRegistry
from lifeos.domain.checkin import CheckInEntry
from lifeos.ports.llm import ChatMessage, ModelOutputError
from lifeos.ports.vault import VaultPath
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.fakes import FakeClock, FakeLlmClient, MemoryAuditLog, MemoryUsageLog, ok_response

pytestmark = [pytest.mark.integration, pytest.mark.security]

CTX = CallContext(agent_id="coach", ritual="checkin", profile="ligero", correlation_id="c-5")
ACTOR = ActorContext(agent_id="coach", correlation_id="c-5")
MESSAGES = (ChatMessage("user", "Resume mi avance en JSON."),)
DAILY = "Diario/2026-10-27.md"
DAILY_TEXT = "# Martes\n## Check-ins\n- 08:00 — Empecé\n"


def _gateway(*responses: object) -> tuple[ModelGateway, MemoryUsageLog]:
    clock = FakeClock()
    log = MemoryUsageLog()
    client = FakeLlmClient(script=list(responses), clock=clock)  # type: ignore[arg-type]
    catalog = parse_catalog(samples.dump(samples.catalog()).encode())
    return ModelGateway({"nvidia": client}, catalog, clock, log), log


def test_valid_output_is_returned_as_domain_object_and_logged_ok() -> None:
    gateway, log = _gateway(ok_response('{"hora": "09:30", "texto": "Terminé"}'))
    entry = gateway.complete_structured(CTX, MESSAGES, SCHEMAS["checkin_v1"])
    assert entry == CheckInEntry(time(9, 30), "Terminé")
    assert [r.outcome for r in log.records] == ["ok"]


@pytest.mark.parametrize(
    "response",
    [
        ok_response("no es json"),
        ok_response('{"hora": "09:30", "texto": "x", "extra": 1}'),
        ok_response('{"hora": "09:30", "texto": "Terminé"}', finish_reason="length"),
    ],
)
def test_invalid_or_truncated_output_is_rejected_and_logged(response: object) -> None:
    gateway, log = _gateway(response)
    with pytest.raises(ModelOutputError):
        gateway.complete_structured(CTX, MESSAGES, SCHEMAS["checkin_v1"])
    [record] = log.records
    assert record.outcome == "salida_invalida"
    assert record.tokens_prompt == 12  # el consumo se registra aunque la salida no sirva


def test_invalid_output_is_never_persisted(vault: FilesystemVault, write_note: WriteNote) -> None:
    write_note(DAILY, DAILY_TEXT)
    gateway, _ = _gateway(ok_response('{"hora": "09:30", "texto": "[avance:: 100]"}'))
    audit = MemoryAuditLog()
    tools = ToolRegistry(vault, samples.effective(), audit, FakeClock())

    with pytest.raises(ModelOutputError):
        entry = gateway.complete_structured(CTX, MESSAGES, SCHEMAS["checkin_v1"])
        tools.anexar_checkin(ACTOR, DAILY, entry)

    assert vault.read(VaultPath(DAILY)).text == DAILY_TEXT
    assert audit.records == []  # ni siquiera hubo intento de escritura


def test_valid_output_flows_into_the_permitted_tool(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(DAILY, DAILY_TEXT)
    gateway, _ = _gateway(ok_response('{"hora": "09:30", "texto": "Terminé"}'))
    tools = ToolRegistry(vault, samples.effective(), MemoryAuditLog(), FakeClock())
    entry = gateway.complete_structured(CTX, MESSAGES, SCHEMAS["checkin_v1"])
    tools.anexar_checkin(ACTOR, DAILY, entry)
    assert vault.read(VaultPath(DAILY)).text.endswith("- 09:30 — Terminé\n")
