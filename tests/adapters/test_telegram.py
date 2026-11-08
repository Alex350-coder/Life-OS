"""Cliente de la Bot API de Telegram contra un servidor local (ADR-0014, SEC-08, SEC-12).

El token viaja en la ruta de la URL: ningún error, `repr` ni mensaje puede contenerlo.
"""

import json

import pytest

from lifeos.adapters.secrets import Secret
from lifeos.adapters.telegram import (
    MAX_MESSAGE_CHARS,
    MAX_VOICE_BYTES,
    TelegramBotClient,
    TelegramNotifier,
)
from lifeos.ports.chat import ChatError, VoiceNote
from lifeos.ports.notify import NotifyError
from tests.stub_http import Reply, StubServer

pytestmark = [pytest.mark.integration, pytest.mark.security]
TOKEN = "123456:ABC-token-de-prueba_xyz"  # noqa: S105 - token inventado para las pruebas


def ok(result: object) -> Reply:
    return Reply(body=json.dumps({"ok": True, "result": result}).encode())


def client(stub: StubServer) -> TelegramBotClient:
    return TelegramBotClient(Secret(TOKEN), base_url=stub.base_url.removesuffix("/v1"))


def update(update_id: int, chat_id: int, **message: object) -> dict[str, object]:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, **message}}


def test_poll_parses_text_and_voice_messages(stub_server: StubServer) -> None:
    stub_server.replies.append(
        ok(
            [
                update(10, 42, text="hola"),
                update(
                    11,
                    42,
                    voice={"file_id": "f-1", "duration": 4, "file_size": 2048},
                ),
                {"update_id": 12, "edited_message": {"chat": {"id": 42}, "text": "x"}},
            ]
        )
    )
    first, second, other = client(stub_server).poll(offset=10, timeout_s=0)
    assert (first.update_id, first.chat_id, first.text) == (10, 42, "hola")
    assert second.voice == VoiceNote("f-1", 4, 2048)
    # Las actualizaciones que no son mensajes se devuelven para avanzar el offset.
    assert (other.update_id, other.text, other.voice) == (12, None, None)
    request = stub_server.received[0]
    assert request.path == f"/bot{TOKEN}/getUpdates"
    assert json.loads(request.body) == {"offset": 10, "timeout": 0, "allowed_updates": ["message"]}


def test_send_uses_plain_text_and_splits_long_messages(stub_server: StubServer) -> None:
    stub_server.replies += [ok({}), ok({})]
    text = "\n".join(["línea"] * (MAX_MESSAGE_CHARS // 4))
    client(stub_server).send(42, text)
    bodies = [json.loads(r.body) for r in stub_server.received]
    assert len(bodies) == 2
    assert all(len(b["text"]) <= MAX_MESSAGE_CHARS for b in bodies)
    assert all("parse_mode" not in b for b in bodies)
    assert "\n".join(b["text"] for b in bodies) == text


@pytest.mark.parametrize("status", [401, 404, 409, 500])
def test_http_errors_never_reveal_the_token(stub_server: StubServer, status: int) -> None:
    stub_server.replies.append(Reply(status=status, body=b'{"ok":false,"description":"bad"}'))
    with pytest.raises(ChatError) as error:
        client(stub_server).poll(offset=None, timeout_s=0)
    assert TOKEN not in str(error.value)
    assert str(status) in str(error.value)


def test_connection_errors_never_reveal_the_token() -> None:
    dead = TelegramBotClient(Secret(TOKEN), base_url="http://127.0.0.1:9")
    with pytest.raises(ChatError) as error:
        dead.poll(offset=None, timeout_s=0)
    assert TOKEN not in str(error.value)
    assert TOKEN not in repr(dead)


def test_api_level_errors_are_reported(stub_server: StubServer) -> None:
    stub_server.replies.append(Reply(body=b'{"ok": false, "error_code": 400}'))
    with pytest.raises(ChatError, match="400"):
        client(stub_server).send(42, "hola")


@pytest.mark.parametrize(
    "body",
    [b"no es json", b"[]", b'{"ok": true, "result": "x"}', b'{"ok": true, "result": [{"x": 1}]}'],
)
def test_malformed_responses_are_rejected(stub_server: StubServer, body: bytes) -> None:
    stub_server.replies.append(Reply(body=body))
    with pytest.raises(ChatError):
        client(stub_server).poll(offset=None, timeout_s=0)


def test_oversized_responses_are_rejected(stub_server: StubServer) -> None:
    stub_server.replies.append(
        Reply(body=b'{"ok": true, "result": [' + b" " * (2 * 1024 * 1024) + b"]}")
    )
    with pytest.raises(ChatError, match="tamaño"):
        client(stub_server).poll(offset=None, timeout_s=0)


def test_only_https_or_loopback_is_accepted() -> None:
    with pytest.raises(ValueError, match="https"):
        TelegramBotClient(Secret(TOKEN), base_url="http://api.telegram.org")


def test_voice_is_downloaded_from_the_file_endpoint(stub_server: StubServer) -> None:
    stub_server.replies += [ok({"file_path": "voice/file_1.oga"}), Reply(body=b"OggS-audio")]
    audio = client(stub_server).download_voice(VoiceNote("f-1", 4, 10))
    assert audio == b"OggS-audio"
    assert stub_server.received[1].path == f"/file/bot{TOKEN}/voice/file_1.oga"


def test_voice_larger_than_the_limit_is_not_downloaded(stub_server: StubServer) -> None:
    with pytest.raises(ChatError, match="grande"):
        client(stub_server).download_voice(VoiceNote("f-1", 4, MAX_VOICE_BYTES + 1))
    assert stub_server.received == []


def test_streamed_voice_is_capped_even_if_the_size_was_not_declared(
    stub_server: StubServer,
) -> None:
    big = b"x" * (MAX_VOICE_BYTES + 1)
    stub_server.replies += [ok({"file_path": "voice/a.oga"}), Reply(body=big, chunks=8)]
    with pytest.raises(ChatError, match="grande"):
        client(stub_server).download_voice(VoiceNote("f-1", 4, None))


@pytest.mark.parametrize("file_path", ["../../etc/passwd", "voice/a b.oga", "", "/abs.oga"])
def test_suspicious_file_paths_are_rejected(stub_server: StubServer, file_path: str) -> None:
    stub_server.replies.append(ok({"file_path": file_path}))
    with pytest.raises(ChatError):
        client(stub_server).download_voice(VoiceNote("f-1", 4, 10))
    assert len(stub_server.received) == 1


def test_notifier_sends_to_the_owner_and_maps_errors(stub_server: StubServer) -> None:
    stub_server.replies += [ok({}), Reply(status=502)]
    notifier = TelegramNotifier(client(stub_server), chat_id=42)
    notifier.send("hola")
    assert json.loads(stub_server.received[0].body)["chat_id"] == 42
    with pytest.raises(NotifyError) as error:
        notifier.send("otra")
    assert TOKEN not in str(error.value)
