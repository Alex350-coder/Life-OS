"""Contratos del calendario externo (ADR-0017, SEC-13).

Lectura y escritura son puertos distintos, con credenciales distintas: quien solo lee la
disponibilidad nunca recibe un objeto capaz de escribir.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from lifeos.domain.calendar_sync import CalendarEntry, PublishedEntry
from lifeos.domain.errors import LifeOSError
from lifeos.domain.external import ExternalBusy


class CalendarError(LifeOSError):
    """El calendario no respondió como se esperaba. El mensaje nunca contiene tokens."""


class CalendarAuthError(CalendarError):
    """Falta la autorización, se revocó o concede más de lo pedido: hay que volver a conectar."""


class BusyReader(Protocol):
    """Intervalos ocupados del calendario del propietario entre `start` y `end`."""

    def busy(self, start: datetime, end: datetime) -> tuple[ExternalBusy, ...]: ...


class CalendarWriter(Protocol):
    """Eventos de Life OS en su propio calendario (scope `calendar.app.created`)."""

    def list_entries(
        self, week_label: str, start: datetime, end: datetime
    ) -> tuple[PublishedEntry, ...]: ...

    def create(self, week_label: str, entry: CalendarEntry) -> None: ...

    def update(self, event_id: str, week_label: str, entry: CalendarEntry) -> None: ...

    def delete(self, event_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class GrantStatus:
    grant: str  # "lectura" o "escritura"
    scope: str
    connected: bool
    obtained: datetime | None
    problem: str | None = None  # p. ej., archivo del token dañado


@dataclass(frozen=True, slots=True)
class RevokeReport:
    revoked: tuple[str, ...]  # permisos revocados ahora en Google
    already_invalid: tuple[str, ...]  # Google ya no los reconocía
    removed: tuple[str, ...]  # tokens locales borrados


class CalendarAccess(Protocol):
    """Autorización del propietario: conectar, consultar y revocar (SEC-13)."""

    def connect(self, grant: str, open_browser: Callable[[str], object]) -> GrantStatus: ...

    def status(self) -> tuple[GrantStatus, ...]: ...

    def revoke(self) -> RevokeReport: ...
