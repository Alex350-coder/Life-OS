"""Sincronización del plan aprobado con el calendario externo (F7-T03, ADR-0017).

La clave de cada evento sale del bloque (semana, tarea, inicio y fin), así que republicar el
mismo plan no crea nada nuevo; un bloque movido sustituye al anterior y un duplicado colado se
elimina.
"""

import pytest

from lifeos.domain.calendar_sync import (
    EVENT_PREFIX,
    CalendarEntry,
    PublishedEntry,
    desired_entries,
    entry_key,
    plan_sync,
)
from lifeos.domain.plan import Block, BlockKind
from tests.planning_builders import MON, TUE, WEEK, span, work

pytestmark = pytest.mark.unit
BLOCK = work(MON, "14:00–15:00", "t-aaaaaa", "Informe")
OTHER = work(TUE, "09:00–10:00", "t-bbbbbb", "Lectura")
FIXED = Block(BlockKind.FIJO, "Clase", span(MON, "10:00–12:00"))


def published(entry: CalendarEntry, event_id: str = "evt1", **changes: object) -> PublishedEntry:
    data = {"key": entry.key, "title": entry.title, "interval": entry.interval, **changes}
    return PublishedEntry(event_id=event_id, **data)  # type: ignore[arg-type]


def test_only_work_blocks_are_published() -> None:
    entries = desired_entries(WEEK, (FIXED, BLOCK))
    assert [e.title for e in entries] == [f"{EVENT_PREFIX}Informe"]


def test_key_is_stable_and_depends_on_task_and_times() -> None:
    assert entry_key(WEEK, BLOCK) == entry_key(WEEK, BLOCK)
    moved = work(MON, "15:00–16:00", "t-aaaaaa", "Informe")
    assert entry_key(WEEK, moved) != entry_key(WEEK, BLOCK)
    assert len(entry_key(WEEK, BLOCK)) == 32
    assert entry_key(WEEK, BLOCK).isalnum()


def test_first_publication_creates_every_block() -> None:
    sync = plan_sync(desired_entries(WEEK, (BLOCK, OTHER)), ())
    assert len(sync.create) == 2
    assert (sync.update, sync.delete, sync.unchanged) == ((), (), 2 * 0)


def test_republishing_the_same_plan_creates_nothing() -> None:
    desired = desired_entries(WEEK, (BLOCK, OTHER))
    sync = plan_sync(desired, tuple(published(e, f"evt{i}") for i, e in enumerate(desired)))
    assert (sync.create, sync.update, sync.delete) == ((), (), ())
    assert sync.unchanged == 2
    assert sync.is_empty


def test_moved_block_replaces_the_old_event() -> None:
    old = desired_entries(WEEK, (BLOCK,))[0]
    moved = desired_entries(WEEK, (work(MON, "16:00–17:00", "t-aaaaaa", "Informe"),))
    sync = plan_sync(moved, (published(old, "old"),))
    assert [e.interval for e in sync.create] == [moved[0].interval]
    assert [p.event_id for p in sync.delete] == ["old"]


def test_edited_title_in_the_calendar_is_restored() -> None:
    entry = desired_entries(WEEK, (BLOCK,))[0]
    sync = plan_sync((entry,), (published(entry, "e1", title="cambiado a mano"),))
    assert sync.update == (("e1", entry),)
    assert sync.create == () and sync.delete == ()


def test_duplicate_events_with_the_same_key_are_removed() -> None:
    entry = desired_entries(WEEK, (BLOCK,))[0]
    sync = plan_sync((entry,), (published(entry, "a"), published(entry, "b")))
    assert sync.create == ()
    assert [p.event_id for p in sync.delete] == ["b"]
    assert sync.unchanged == 1


def test_block_removed_from_the_plan_is_deleted() -> None:
    entry = desired_entries(WEEK, (BLOCK,))[0]
    sync = plan_sync((), (published(entry, "gone"),))
    assert [p.event_id for p in sync.delete] == ["gone"]


def test_title_is_inert_single_line() -> None:
    block = work(MON, "14:00–15:00", "t-aaaaaa", "Informe‮ [x]\nfin")
    title = desired_entries(WEEK, (block,))[0].title
    assert "‮" not in title and "\n" not in title and "[" not in title
