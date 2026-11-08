"""Dobles del canal de Telegram y del transcriptor para las pruebas del servicio (inventados)."""

from dataclasses import dataclass, field

from lifeos.ports.chat import ChatError, IncomingMessage, VoiceNote
from lifeos.ports.transcribe import TranscriptionError

OWNER = 4242
STRANGER = 9999


def text(update_id: int, body: str | None, chat_id: int | None = OWNER) -> IncomingMessage:
    return IncomingMessage(update_id, chat_id, body, None)


def voice(
    update_id: int, *, duration: int = 5, size: int | None = 1000, chat_id: int = OWNER
) -> IncomingMessage:
    return IncomingMessage(update_id, chat_id, None, VoiceNote("f-1", duration, size))


@dataclass
class FakeChannel:
    batches: list[tuple[IncomingMessage, ...] | ChatError] = field(default_factory=list)
    sent: list[tuple[int, str]] = field(default_factory=list)
    polls: list[int | None] = field(default_factory=list)
    downloads: int = 0

    def poll(self, offset: int | None, timeout_s: int) -> tuple[IncomingMessage, ...]:
        self.polls.append(offset)
        if not self.batches:
            return ()
        batch = self.batches.pop(0)
        if isinstance(batch, ChatError):
            raise batch
        return batch

    def send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    def download_voice(self, voice: VoiceNote) -> bytes:
        self.downloads += 1
        return b"OggS-audio"


@dataclass
class FakeTranscriber:
    result: str | TranscriptionError = "Comprar cuerdas para la guitarra"
    calls: list[bytes] = field(default_factory=list)

    def transcribe(self, audio: bytes) -> str:
        self.calls.append(audio)
        if isinstance(self.result, TranscriptionError):
            raise self.result
        return self.result
