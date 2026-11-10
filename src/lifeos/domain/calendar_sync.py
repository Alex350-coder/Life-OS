"""Qué crear, actualizar o borrar en el calendario de Life OS (F7-T03, ADR-0017).

Cada bloque de trabajo aprobado se publica como un evento con una clave privada calculada a
partir de la semana, la tarea y el intervalo. Comparar por clave hace la publicación idempotente:
republicar el mismo plan no crea nada, un bloque movido sustituye al anterior y un evento
duplicado (p. ej., tras un fallo a mitad) se elimina. Solo se tocan eventos de la semana que
llevan la marca de Life OS, en el calendario propio de Life OS.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from lifeos.domain.capacity import Interval
from lifeos.domain.plan import Block, BlockKind, clean
from lifeos.domain.weeks import IsoWeek

EVENT_PREFIX = "Life OS · "
KEY_CHARS = 32
MAX_EVENT_TITLE_CHARS = 120


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    """Evento que debe existir: un bloque de trabajo aprobado."""

    key: str
    title: str
    interval: Interval


@dataclass(frozen=True, slots=True)
class PublishedEntry:
    """Evento que ya existe en el calendario de Life OS."""

    event_id: str
    key: str
    title: str
    interval: Interval


@dataclass(frozen=True, slots=True)
class SyncPlan:
    create: tuple[CalendarEntry, ...]
    update: tuple[tuple[str, CalendarEntry], ...]  # (id del evento, contenido esperado)
    delete: tuple[PublishedEntry, ...]
    unchanged: int

    @property
    def is_empty(self) -> bool:
        return not (self.create or self.update or self.delete)


def entry_key(week: IsoWeek, block: Block) -> str:
    start = block.interval.start.timestamp()
    end = block.interval.end.timestamp()
    raw = f"{week.render()}|{block.task_id}|{start:.0f}|{end:.0f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:KEY_CHARS]


def desired_entries(week: IsoWeek, blocks: Iterable[Block]) -> tuple[CalendarEntry, ...]:
    """Solo los bloques de trabajo: lo fijo y lo protegido ya es del propietario."""
    work = sorted(
        (b for b in blocks if b.kind is BlockKind.TRABAJO),
        key=lambda b: b.interval.start.timestamp(),
    )
    return tuple(
        CalendarEntry(
            entry_key(week, block),
            EVENT_PREFIX + clean(block.title, MAX_EVENT_TITLE_CHARS - len(EVENT_PREFIX)),
            block.interval,
        )
        for block in work
    )


def _same_time(a: Interval, b: Interval) -> bool:
    return a.start.timestamp() == b.start.timestamp() and a.end.timestamp() == b.end.timestamp()


def plan_sync(desired: Iterable[CalendarEntry], published: Iterable[PublishedEntry]) -> SyncPlan:
    wanted = {entry.key: entry for entry in desired}
    seen: set[str] = set()
    update: list[tuple[str, CalendarEntry]] = []
    delete: list[PublishedEntry] = []
    unchanged = 0
    for event in published:
        entry = wanted.get(event.key)
        if entry is None or event.key in seen:
            delete.append(event)  # fuera del plan o duplicado de una clave ya vista
            continue
        seen.add(event.key)
        if event.title == entry.title and _same_time(event.interval, entry.interval):
            unchanged += 1
        else:
            update.append((event.event_id, entry))
    create = tuple(entry for key, entry in wanted.items() if key not in seen)
    return SyncPlan(create, tuple(update), tuple(delete), unchanged)
