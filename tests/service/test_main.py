"""Entrada `lifeos-servicio`: configuración, --descubrir, --una-vez y candado (ADR-0014)."""

import io
import json
import shutil
from pathlib import Path

import pytest

from lifeos.adapters.process_lock import ProcessLock
from lifeos.service.main import EXIT_CONFIG, EXIT_OK, EXIT_RUNNING, main
from tests.planning_samples import HORARIO
from tests.stub_http import Reply, StubServer

pytestmark = pytest.mark.integration
PROJECT = Path(__file__).resolve().parents[2]
TOKEN = "222222:MAIN-token-inventado_abc"  # noqa: S105 - token inventado para las pruebas
OWNER = 4242
RITUALES = "## Rituales\n- cierre · lun-dom 19:00\n"


def ok(result: object) -> Reply:
    return Reply(body=json.dumps({"ok": True, "result": result}).encode())


@pytest.fixture
def setup(tmp_path: Path, stub_server: StubServer) -> tuple[Path, dict[str, str]]:
    project = tmp_path / "project"
    shutil.copytree(PROJECT / "config", project / "config")
    vault = tmp_path / "vault"
    (vault / "_sistema").mkdir(parents=True)
    (vault / "_sistema" / "horario.md").write_text(HORARIO, encoding="utf-8")
    (vault / "_sistema" / "rituales.md").write_text(RITUALES, encoding="utf-8")
    env = {
        "LIFEOS_VAULT_PATH": str(vault),
        "TELEGRAM_BOT_TOKEN": TOKEN,
        "TELEGRAM_OWNER_CHAT_ID": str(OWNER),
        "LIFEOS_TELEGRAM_API": stub_server.base_url.removesuffix("/v1"),
        "LIFEOS_LOGS_DIR": str(tmp_path / "logs"),
        "LIFEOS_STATE_DIR": str(tmp_path / "state"),
    }
    return project, env


def test_missing_configuration_exits_with_code_2(
    setup: tuple[Path, dict[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    project, env = setup
    env.pop("TELEGRAM_BOT_TOKEN")
    assert main(["--una-vez"], environ=env, project_root=project) == EXIT_CONFIG
    err = capsys.readouterr().err
    assert "TELEGRAM_BOT_TOKEN" in err


def test_one_iteration_answers_the_owner_and_captures(
    setup: tuple[Path, dict[str, str]], stub_server: StubServer, tmp_path: Path
) -> None:
    project, env = setup
    update = {"update_id": 7, "message": {"chat": {"id": OWNER}, "text": "comprar cuerdas"}}
    stub_server.replies += [ok([update]), ok({})]
    out = io.StringIO()
    assert main(["--una-vez"], environ=env, project_root=project, out=out) == EXIT_OK
    assert "en marcha" in out.getvalue()
    (reply,) = [json.loads(r.body) for r in stub_server.received if r.path.endswith("sendMessage")]
    assert reply["chat_id"] == OWNER
    assert len(list((tmp_path / "vault" / "00-Inbox").glob("*.md"))) == 1


def test_discover_prints_chat_ids_without_message_content(
    setup: tuple[Path, dict[str, str]], stub_server: StubServer
) -> None:
    project, env = setup
    env.pop("TELEGRAM_OWNER_CHAT_ID")
    env.pop("LIFEOS_VAULT_PATH")  # --descubrir no necesita la bóveda ni el chat_id
    update = {"update_id": 1, "message": {"chat": {"id": 555}, "text": "/start secreto"}}
    stub_server.replies.append(ok([update]))
    out = io.StringIO()
    assert main(["--descubrir"], environ=env, project_root=project, out=out) == EXIT_OK
    assert "chat_id: 555" in out.getvalue()
    assert "secreto" not in out.getvalue()


def test_discover_without_messages_says_so(
    setup: tuple[Path, dict[str, str]], stub_server: StubServer
) -> None:
    project, env = setup
    stub_server.replies.append(ok([]))
    out = io.StringIO()
    assert main(["--descubrir"], environ=env, project_root=project, out=out) == EXIT_OK
    assert "No llegó ningún mensaje" in out.getvalue()


def test_discover_reports_telegram_errors(
    setup: tuple[Path, dict[str, str]], stub_server: StubServer, capsys: pytest.CaptureFixture[str]
) -> None:
    project, env = setup
    stub_server.replies.append(Reply(status=401))
    assert main(["--descubrir"], environ=env, project_root=project) == EXIT_CONFIG
    assert TOKEN not in capsys.readouterr().err


def test_a_second_service_exits_with_code_3(
    setup: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    project, env = setup
    with ProcessLock(tmp_path / "state" / "servicio.lock"):
        assert main(["--una-vez"], environ=env, project_root=project) == EXIT_RUNNING
