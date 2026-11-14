"""Publicación aprobada en Google Calendar sin duplicados (F7-T03, ADR-0017, RULES G.4–G.5).

Solo se publica un plan semanal aprobado; la vista previa no escribe nada y republicar el mismo
plan no crea eventos nuevos.
"""

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.calendar_publication import CalendarNotApprovedError, CalendarPublisher
from lifeos.application.calendar_tools import CalendarTools
from lifeos.application.jefe import JefePlanner
from lifeos.application.tools import ToolRegistry
from lifeos.domain.calendar_sync import EVENT_PREFIX
from lifeos.domain.plan import BlockKind
from lifeos.ports.logs import AuditResult
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.calendar_fakes import FakeCalendarWriter
from tests.fakes import FakeClock, MemoryAuditLog
from tests.planning_builders import NOW, WEEK
from tests.planning_samples import HORARIO

pytestmark = [pytest.mark.integration, pytest.mark.security]
TASKS = (
    "- [ ] Informe [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 2h] 📅 2026-11-04\n"
    "- [ ] Lectura [id:: t-bbbbbb] [area:: universidad] [esfuerzo:: 1h]\n"
)


@pytest.fixture
def audit() -> MemoryAuditLog:
    return MemoryAuditLog()


@pytest.fixture
def writer() -> FakeCalendarWriter:
    return FakeCalendarWriter()


@pytest.fixture
def jefe(vault: FilesystemVault, audit: MemoryAuditLog, write_note: WriteNote) -> JefePlanner:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    clock = FakeClock(NOW)
    return JefePlanner(ToolRegistry(vault, samples.effective(), audit, clock), clock)


@pytest.fixture
def publisher(
    jefe: JefePlanner, audit: MemoryAuditLog, writer: FakeCalendarWriter
) -> CalendarPublisher:
    calendar = CalendarTools(samples.effective(), audit, FakeClock(NOW), writer=writer)
    return CalendarPublisher(jefe, calendar, FakeClock(NOW))


def test_nothing_is_published_without_a_plan(
    publisher: CalendarPublisher, writer: FakeCalendarWriter
) -> None:
    with pytest.raises(CalendarNotApprovedError, match="no hay plan"):
        publisher.publish(WEEK)
    assert writer.operations == []


def test_a_proposed_plan_is_never_published(
    jefe: JefePlanner, publisher: CalendarPublisher, writer: FakeCalendarWriter
) -> None:
    jefe.proponer(WEEK)
    with pytest.raises(CalendarNotApprovedError, match="aprobar"):
        publisher.preview(WEEK)
    with pytest.raises(CalendarNotApprovedError):
        publisher.publish(WEEK)
    assert writer.operations == []


def test_preview_shows_changes_without_writing(
    jefe: JefePlanner, publisher: CalendarPublisher, writer: FakeCalendarWriter
) -> None:
    jefe.proponer(WEEK)
    approved = jefe.aprobar(WEEK)
    work = [b for b in approved.proposal.blocks if b.kind is BlockKind.TRABAJO]
    preview = publisher.preview(WEEK)
    assert len(preview.sync.create) == len(work) > 0
    assert all(entry.title.startswith(EVENT_PREFIX) for entry in preview.sync.create)
    assert writer.operations == []


def test_publishing_twice_creates_no_duplicates(
    jefe: JefePlanner,
    publisher: CalendarPublisher,
    writer: FakeCalendarWriter,
    audit: MemoryAuditLog,
) -> None:
    jefe.proponer(WEEK)
    jefe.aprobar(WEEK)
    first = publisher.publish(WEEK)
    count = len(writer.events)
    assert first.applied.created == count > 0
    second = publisher.publish(WEEK)
    assert (second.applied.created, second.applied.updated, second.applied.deleted) == (0, 0, 0)
    assert second.preview.sync.unchanged == count
    assert len(writer.events) == count
    oks = [r for r in audit.records if r.action == "publicar_calendario"]
    assert {r.actor for r in oks} == {"jefe"}
    assert sum(r.result is AuditResult.OK for r in oks) == count


def test_event_deleted_by_hand_is_recreated_once(
    jefe: JefePlanner, publisher: CalendarPublisher, writer: FakeCalendarWriter
) -> None:
    jefe.proponer(WEEK)
    jefe.aprobar(WEEK)
    publisher.publish(WEEK)
    count = len(writer.events)
    writer.events.pop(next(iter(writer.events)))
    result = publisher.publish(WEEK)
    assert result.applied.created == 1
    assert len(writer.events) == count
