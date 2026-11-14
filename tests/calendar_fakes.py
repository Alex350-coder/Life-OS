"""Calendario en memoria para las pruebas (nunca se usa fuera de tests). Datos inventados."""

from dataclasses import dataclass, field
from datetime import datetime

from lifeos.domain.calendar_sync import CalendarEntry, PublishedEntry
from lifeos.domain.external import ExternalBusy
from lifeos.ports.calendar import CalendarError


@dataclass
class FakeBusyReader:
    items: tuple[ExternalBusy, ...] = ()
    error: CalendarError | None = None
    calls: list[tuple[datetime, datetime]] = field(default_factory=list)

    def busy(self, start: datetime, end: datetime) -> tuple[ExternalBusy, ...]:
        self.calls.append((start, end))
        if self.error is not None:
            raise self.error
        return self.items


@dataclass
class FakeCalendarWriter:
    """Guarda eventos por id con la semana a la que pertenecen. `fail_after` corta a mitad."""

    events: dict[str, tuple[str, PublishedEntry]] = field(default_factory=dict)
    fail_after: int | None = None
    operations: list[str] = field(default_factory=list)
    _next: int = 0

    def _tick(self, name: str) -> None:
        if self.fail_after is not None and len(self.operations) >= self.fail_after:
            raise CalendarError("el calendario no respondió (prueba)")
        self.operations.append(name)

    def list_entries(
        self, week_label: str, start: datetime, end: datetime
    ) -> tuple[PublishedEntry, ...]:
        return tuple(entry for week, entry in self.events.values() if week == week_label)

    def create(self, week_label: str, entry: CalendarEntry) -> None:
        self._tick("create")
        self._next += 1
        event_id = f"evt{self._next}"
        self.events[event_id] = (
            week_label,
            PublishedEntry(event_id, entry.key, entry.title, entry.interval),
        )

    def update(self, event_id: str, week_label: str, entry: CalendarEntry) -> None:
        self._tick("update")
        self.events[event_id] = (
            week_label,
            PublishedEntry(event_id, entry.key, entry.title, entry.interval),
        )

    def delete(self, event_id: str) -> None:
        self._tick("delete")
        self.events.pop(event_id, None)
