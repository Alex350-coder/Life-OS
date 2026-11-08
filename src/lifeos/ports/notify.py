"""Puerto de notificaciones al propietario (ADR-0014). El adaptador real es Telegram."""

from typing import Protocol

from lifeos.domain.errors import LifeOSError


class NotifyError(LifeOSError):
    """No se pudo entregar la notificación. El mensaje nunca incluye secretos."""


class Notifier(Protocol):
    def send(self, text: str) -> None: ...
