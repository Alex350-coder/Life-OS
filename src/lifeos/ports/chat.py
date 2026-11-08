"""Puerto del canal de mensajería con el propietario (ADR-0014). El adaptador real es Telegram."""

from dataclasses import dataclass
from typing import Protocol

from lifeos.domain.errors import LifeOSError


class ChatError(LifeOSError):
    """Fallo del canal. El mensaje nunca incluye el token ni el contenido recibido."""


@dataclass(frozen=True, slots=True)
class VoiceNote:
    file_id: str
    duration_s: int
    size: int | None  # tamaño declarado por el servicio; puede faltar


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    """Una actualización recibida. Sin `chat_id` ni contenido si no era un mensaje."""

    update_id: int
    chat_id: int | None
    text: str | None
    voice: VoiceNote | None


class ChatChannel(Protocol):
    def poll(self, offset: int | None, timeout_s: int) -> tuple[IncomingMessage, ...]: ...

    def send(self, chat_id: int, text: str) -> None: ...

    def download_voice(self, voice: VoiceNote) -> bytes: ...
