"""SEC-12 de extremo a extremo: servicio real contra una Bot API local (ADR-0014).

Un mensaje de otro `chat_id` se ignora sin respuesta y queda en la auditoría sin su contenido ni
el `chat_id` en claro; el token nunca aparece en los logs.
"""

import json
import shutil
from pathlib import Path

import pytest

from lifeos.bootstrap import Settings, build_service
from lifeos.ports.config import ConfigRejectedError
from lifeos.service.bot import OwnerBot
from tests.planning_samples import HORARIO
from tests.stub_http import Reply, StubServer

pytestmark = [pytest.mark.integration, pytest.mark.security]
PROJECT = Path(__file__).resolve().parents[2]
TOKEN = "777000:SEC12-token-inventado_abc"  # noqa: S105 - token inventado para las pruebas
OWNER = 4242
STRANGER = 31337


@pytest.fixture
def env(tmp_path: Path, stub_server: StubServer) -> dict[str, str]:
    project = tmp_path / "project"
    shutil.copytree(PROJECT / "config", project / "config")
    vault = tmp_path / "vault"
    (vault / "_sistema").mkdir(parents=True)
    (vault / "_sistema" / "horario.md").write_text(HORARIO, encoding="utf-8")
    return {
        "root": str(project),
        "LIFEOS_VAULT_PATH": str(vault),
        "TELEGRAM_BOT_TOKEN": TOKEN,
        "TELEGRAM_OWNER_CHAT_ID": str(OWNER),
        "LIFEOS_TELEGRAM_API": stub_server.base_url.removesuffix("/v1"),
        "LIFEOS_LOGS_DIR": str(tmp_path / "logs"),
        "LIFEOS_STATE_DIR": str(tmp_path / "state"),
    }


def _settings(env: dict[str, str]) -> Settings:
    values = {k: v for k, v in env.items() if k != "root"}
    return Settings.from_env(Path(env["root"]), values)


def _updates(*messages: tuple[int, int, str]) -> Reply:
    result = [
        {"update_id": uid, "message": {"chat": {"id": chat}, "text": body}}
        for uid, chat, body in messages
    ]
    return Reply(body=json.dumps({"ok": True, "result": result}).encode())


def test_foreign_chat_is_ignored_and_audited_end_to_end(
    env: dict[str, str], stub_server: StubServer, tmp_path: Path
) -> None:
    service = build_service(_settings(env))
    bot = OwnerBot(
        service.channel,
        service.owner_chat_id,
        vault=service.runtime.vault,  # type: ignore[arg-type]
        coach=service.runtime.coach,  # type: ignore[arg-type]
        clock=service.runtime.clock,
        audit=service.runtime.audit_log,
        transcriber=None,
        fingerprint_key=service.fingerprint_key,
    )
    stub_server.replies.append(_updates((1, STRANGER, "secreto del intruso")))
    (message,) = service.channel.poll(None, 0)
    bot.handle(message)
    assert [r.path for r in stub_server.received] == [f"/bot{TOKEN}/getUpdates"]  # sin respuesta
    logs = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "logs").glob("*.jsonl"))
    assert "mensaje_ajeno" in logs
    assert "secreto del intruso" not in logs
    assert str(STRANGER) not in logs
    assert TOKEN not in logs
    assert not list((tmp_path / "vault").glob("00-Inbox/*.md"))


def test_service_requires_the_owner_chat_id(env: dict[str, str]) -> None:
    env.pop("TELEGRAM_OWNER_CHAT_ID")
    with pytest.raises(ConfigRejectedError, match="TELEGRAM_OWNER_CHAT_ID"):
        build_service(_settings(env))


@pytest.mark.parametrize("value", ["abc", "0", "-5", "1" * 25])
def test_invalid_owner_chat_id_is_rejected(env: dict[str, str], value: str) -> None:
    env["TELEGRAM_OWNER_CHAT_ID"] = value
    with pytest.raises(ConfigRejectedError, match="TELEGRAM_OWNER_CHAT_ID"):
        build_service(_settings(env))


def test_service_requires_the_vault(env: dict[str, str]) -> None:
    env.pop("LIFEOS_VAULT_PATH")
    with pytest.raises(ConfigRejectedError, match="LIFEOS_VAULT_PATH"):
        build_service(_settings(env))


def test_unknown_whisper_model_is_rejected(env: dict[str, str]) -> None:
    env["LIFEOS_WHISPER_MODEL"] = "gigante"
    with pytest.raises(ConfigRejectedError, match="LIFEOS_WHISPER_MODEL"):
        build_service(_settings(env))
