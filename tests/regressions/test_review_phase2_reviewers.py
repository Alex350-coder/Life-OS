"""Regresiones de los revisores de la Fase 2 (code, security, python, silent-failure, type-design).

Cada prueba fija un hallazgo concreto; primero se confirmó en rojo y después se corrigió.
"""

from dataclasses import dataclass, replace
from datetime import time
from decimal import Decimal
from pathlib import Path

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.jsonl_logs import JsonlAuditLog
from lifeos.adapters.nvidia_llm import NvidiaChatClient
from lifeos.adapters.schemas import SCHEMAS
from lifeos.adapters.secrets import Secret, read_dotenv
from lifeos.adapters.yaml_config import parse_catalog, parse_policy
from lifeos.application.llm_calls import CallContext, ModelGateway, UsageNotRecordedError
from lifeos.application.permissions import TOOL_WRITE_MODES, resolve_effective
from lifeos.application.policy_vault import PolicyVault
from lifeos.application.tools import ActorContext, AuditIncompleteError, ToolRegistry
from lifeos.bootstrap import Settings
from lifeos.domain.checkin import CheckInEntry
from lifeos.domain.sections import only_inserted_in_section
from lifeos.ports.config import ConfigRejectedError, WriteGrant, WriteMode
from lifeos.ports.llm import (
    ChatMessage,
    LlmError,
    LlmRequest,
    LlmResponseError,
    ModelOutputError,
)
from lifeos.ports.logs import (
    AuditRecord,
    AuditResult,
    CostStatus,
    LogWriteError,
    UsageOutcome,
    UsageRecord,
)
from lifeos.ports.vault import PathRejectedError, Snapshot, VaultPath, VaultStore, Version
from tests import config_samples as samples
from tests.conftest import WriteNote
from tests.fakes import (
    FIXED_NOW,
    FakeClock,
    FakeLlmClient,
    MemoryAuditLog,
    MemoryUsageLog,
    ok_response,
)
from tests.stub_http import Reply, StubServer

pytestmark = [pytest.mark.security]

KEY = "nvapi" + "-TESTKEY-0123456789abcdefXYZ"  # en dos partes: sin patrón real en el archivo
DAILY = "Diario/2026-10-27.md"
DAILY_TEXT = "# Martes\n## Plan\n- bloque\n\n## Check-ins\n- 08:00 — Empecé\n"
COACH = ActorContext("coach", "c-r")
ENTRY = CheckInEntry(time(9, 30), "Terminé")
CTX = CallContext("coach", "r", "ligero", "c-g")
HELLO = (ChatMessage("user", "x"),)


def _tools(vault: VaultStore, audit: MemoryAuditLog | None = None) -> ToolRegistry:
    return ToolRegistry(vault, samples.effective(), audit or MemoryAuditLog(), FakeClock())


def _gateway(*script: object, log: MemoryUsageLog | None = None) -> ModelGateway:
    clock = FakeClock()
    client = FakeLlmClient(script=list(script), clock=clock)  # type: ignore[arg-type]
    catalog = parse_catalog(samples.dump(samples.catalog()).encode())
    return ModelGateway({"nvidia": client}, catalog, clock, log or MemoryUsageLog())


def _request() -> LlmRequest:
    return LlmRequest("m", HELLO, timeout_s=5, max_tokens=8)


# --- security-reviewer ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["ARCHIV~1/x.md", "_SISTE~1/perfil.md", "05-SAL~1/x.md", "a~9b/x.md"]
)
def test_windows_short_names_are_rejected(raw: str) -> None:
    with pytest.raises(PathRejectedError, match=r"8\.3"):
        VaultPath(raw)


def test_tilde_without_digit_is_still_a_valid_name() -> None:
    assert VaultPath("notas~borrador.md").value == "notas~borrador.md"


@pytest.mark.parametrize("line", ["---", "===", "  ***  ", "___"])
def test_append_cannot_turn_previous_line_into_heading_or_rule(line: str) -> None:
    old = "## Check-ins\n- 08:00 — a\n"
    assert not only_inserted_in_section(old, old + line + "\n", "check-ins")


@pytest.mark.parametrize("missing", ["05-Salud/", "06-Finanzas/"])
def test_policy_cannot_drop_the_restricted_read_floor(missing: str) -> None:
    data = samples.policy()
    data["global"]["lectura_restringida"].remove(missing)
    with pytest.raises(ConfigRejectedError, match=missing.rstrip("/")):
        resolve_effective(
            parse_policy(samples.dump(data).encode()),
            parse_catalog(samples.dump(samples.catalog()).encode()),
            (),
            tools=TOOL_WRITE_MODES,
            contracts=samples.KNOWN_CONTRACTS,
        )


def test_escaped_secret_is_still_redacted(tmp_path: Path) -> None:
    secret = 'abc"def\\ghi-1234567890'  # noqa: S105 - valor ficticio
    log = JsonlAuditLog(tmp_path, secrets=(secret,))
    with pytest.raises(LogWriteError):
        log.append(AuditRecord(FIXED_NOW, "c", "coach", "x", secret, AuditResult.OK))


def test_non_ascii_api_key_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="clave"):
        NvidiaChatClient("https://integrate.api.nvidia.com/v1", Secret("clave-ñ-no-ascii"))


# --- python-reviewer ------------------------------------------------------------------------


def test_settings_repr_never_shows_environment_values(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"NVIDIA_API_KEY={KEY}\n", encoding="utf-8")
    settings = Settings.from_env(tmp_path, {})
    assert KEY not in repr(settings)
    assert KEY not in str(settings)


def test_dotenv_with_bom_and_duplicates(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_bytes("﻿A=1\n".encode())
    assert read_dotenv(env) == {"A": "1"}
    env.write_text("A=1\nA=2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicada"):
        read_dotenv(env)


# --- silent-failure-hunter y code-reviewer ----------------------------------------------------


def test_truncated_http_body_is_an_llm_error(stub_server: StubServer) -> None:
    stub_server.replies.append(Reply(body=b'{"choices": [', declared_length=500))
    with pytest.raises(LlmError):
        NvidiaChatClient(stub_server.base_url, Secret(KEY)).complete(_request())


def test_deeply_nested_json_is_rejected_not_crashing(stub_server: StubServer) -> None:
    with pytest.raises(ModelOutputError):
        SCHEMAS["captura_v1"].parse("[" * 60_000)
    stub_server.replies.append(Reply(body=b"[" * 200_000))
    with pytest.raises(LlmResponseError):
        NvidiaChatClient(stub_server.base_url, Secret(KEY)).complete(_request())


class _BrokenSchema:
    id = "roto"

    def parse(self, raw: str) -> object:
        raise RuntimeError("fallo interno")


def test_unexpected_schema_failure_still_records_usage() -> None:
    log = MemoryUsageLog()
    with pytest.raises(RuntimeError):
        _gateway(ok_response(), log=log).complete_structured(CTX, HELLO, _BrokenSchema())
    assert [r.outcome for r in log.records] == [UsageOutcome.ERROR_INTERNO]


def test_usage_log_failure_after_success_keeps_the_paid_response() -> None:
    with pytest.raises(UsageNotRecordedError) as info:
        _gateway(ok_response("pagado"), log=MemoryUsageLog(fail=True)).complete(CTX, HELLO)
    assert info.value.response is not None
    assert info.value.response.text == "pagado"


def test_truncated_plain_completion_is_logged_as_truncated() -> None:
    log = MemoryUsageLog()
    response = _gateway(ok_response(finish_reason="length"), log=log).complete(CTX, HELLO)
    assert response.finish_reason == "length"
    assert [r.outcome for r in log.records] == [UsageOutcome.TRUNCADA]


@dataclass
class _ExplodingVault:
    inner: FilesystemVault

    def read(self, path: VaultPath) -> Snapshot:
        return self.inner.read(path)

    def exists(self, path: VaultPath) -> bool:
        return self.inner.exists(path)

    def list_notes(self, prefix: VaultPath | None = None) -> tuple[VaultPath, ...]:
        return self.inner.list_notes(prefix)

    def write(self, path: VaultPath, text: str, *, expected: Version | None) -> Version:
        raise RuntimeError("fallo inesperado")


def test_unexpected_exception_closes_the_audit_trail(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(DAILY, DAILY_TEXT)
    audit = MemoryAuditLog()
    with pytest.raises(RuntimeError):
        _tools(_ExplodingVault(vault), audit).anexar_checkin(COACH, DAILY, ENTRY)
    assert [r.result for r in audit.records] == [AuditResult.INTENTO, AuditResult.ERROR]
    assert audit.records[-1].reason == "RuntimeError"


def test_missing_ok_audit_reports_that_the_write_happened(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(DAILY, DAILY_TEXT)
    with pytest.raises(AuditIncompleteError) as info:
        _tools(vault, MemoryAuditLog(fail_on=AuditResult.OK)).anexar_checkin(COACH, DAILY, ENTRY)
    assert info.value.written == DAILY
    assert vault.read(VaultPath(DAILY)).text.endswith("- 09:30 — Terminé\n")


def test_jsonl_append_after_truncated_line_starts_on_a_new_line(tmp_path: Path) -> None:
    (tmp_path / "audit-2026-10.jsonl").write_text('{"cortado": ', encoding="utf-8")
    JsonlAuditLog(tmp_path).append(AuditRecord(FIXED_NOW, "c", "coach", "x", "t", AuditResult.OK))
    lines = (tmp_path / "audit-2026-10.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"cortado": '
    assert lines[1].startswith('{"ts":')


def test_several_append_grants_on_the_same_root_are_all_honoured(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    coach = samples.effective()["coach"]
    extra = WriteGrant(VaultPath("Diario"), WriteMode.ANEXAR_SECCION, "notas")
    perms = replace(coach, writes=(*coach.writes, extra))
    write_note(DAILY, DAILY_TEXT + "## Notas\n")
    snapshot = vault.read(VaultPath(DAILY))
    PolicyVault(vault, perms, WriteMode.ANEXAR_SECCION).write(
        VaultPath(DAILY), DAILY_TEXT + "## Notas\n- nota\n", expected=snapshot.version
    )


# --- type-design-analyzer: invariantes en los tipos -------------------------------------------


def test_write_grant_section_must_match_mode() -> None:
    with pytest.raises(ValueError):
        WriteGrant(VaultPath("Diario"), WriteMode.ANEXAR_SECCION)
    with pytest.raises(ValueError):
        WriteGrant(VaultPath("00-Inbox"), WriteMode.CREAR, "x")


def _record(**overrides: object) -> UsageRecord:
    values: dict[str, object] = {
        "ts": FIXED_NOW,
        "correlation_id": "c",
        "provider": "nvidia",
        "model": "m",
        "profile": "ligero",
        "agent": "coach",
        "ritual": "r",
        "attempt": 1,
        "latency_ms": 1,
        "tokens_prompt": None,
        "tokens_completion": None,
        "cost_usd": None,
        "cost_status": CostStatus.DESCONOCIDO,
        "outcome": UsageOutcome.OK,
        "http_status": 200,
    }
    values.update(overrides)
    return UsageRecord(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"attempt": 0},
        {"latency_ms": -1},
        {"tokens_prompt": -5},
        {"cost_usd": Decimal("0.1")},  # costo con estado desconocido
        {"cost_status": CostStatus.ESTIMADO},  # estimado sin costo
        {"cost_usd": Decimal("-1"), "cost_status": CostStatus.ESTIMADO},
    ],
)
def test_usage_record_invariants(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _record(**overrides)
