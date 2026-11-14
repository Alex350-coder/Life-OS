"""El Jefe considera la disponibilidad externa (F7-T02, ADR-0017).

La propuesta evita los intervalos ocupados y los anota con fuente y confianza. La aprobación los
vuelve a leer: un evento nuevo que pisa el plan lo rechaza. Si el calendario no responde, la
propuesta sigue con una alerta, pero la aprobación falla cerrada.
"""

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.calendar_tools import CalendarTools
from lifeos.application.jefe import JefePlanner
from lifeos.application.tasks import ApplicationError
from lifeos.application.tools import ToolRegistry
from lifeos.domain.external import busy_from
from lifeos.domain.plan import BlockKind, ConflictCode, PlanStatus
from lifeos.domain.plan_note import parse_weekly
from lifeos.ports.calendar import CalendarError
from lifeos.ports.logs import AuditResult
from lifeos.ports.vault import VaultPath
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.calendar_fakes import FakeBusyReader
from tests.fakes import FakeClock, MemoryAuditLog
from tests.planning_builders import MON, NOW, WEEK, span
from tests.planning_samples import HORARIO

pytestmark = [pytest.mark.integration, pytest.mark.security]
WEEKLY = VaultPath.parse("Semanal/2026-W45.md")
TASKS = "- [ ] Informe [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 2h] 📅 2026-11-04\n"
# Lunes 08:00–10:00 ocupado: es justo donde la heurística pondría la primera tarea.
MORNING = busy_from(span(MON, "08:00–10:00"), "google:primary")


@pytest.fixture
def audit() -> MemoryAuditLog:
    return MemoryAuditLog()


@pytest.fixture
def reader() -> FakeBusyReader:
    return FakeBusyReader((MORNING,))


@pytest.fixture
def planner(
    vault: FilesystemVault, audit: MemoryAuditLog, reader: FakeBusyReader, write_note: WriteNote
) -> JefePlanner:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    clock = FakeClock(NOW)
    tools = ToolRegistry(vault, samples.effective(), audit, clock)
    calendar = CalendarTools(samples.effective(), audit, clock, reader=reader)
    return JefePlanner(tools, clock, calendar=calendar)


def test_proposal_avoids_external_busy_time(planner: JefePlanner, vault: FilesystemVault) -> None:
    outcome = planner.proponer(WEEK)
    assert outcome.accepted
    work = [b for b in outcome.proposal.blocks if b.kind is BlockKind.TRABAJO]
    assert work and not any(b.interval.overlaps(MORNING.interval) for b in work)
    text = vault.read(WEEKLY).text
    assert "08:00–10:00 · ocupado · fuente: google:primary · confianza: alta" in text


def test_reader_is_asked_for_the_whole_week(planner: JefePlanner, reader: FakeBusyReader) -> None:
    planner.proponer(WEEK)
    start, end = reader.calls[0]
    assert start.date() == WEEK.days()[0] and (start.hour, start.minute) == (0, 0)
    assert end.date() == WEEK.days()[-1].replace(day=WEEK.days()[-1].day + 1)


def test_new_external_event_blocks_the_approval(
    planner: JefePlanner, reader: FakeBusyReader, vault: FilesystemVault
) -> None:
    reader.items = ()
    proposed = planner.proponer(WEEK)
    first = next(b for b in proposed.proposal.blocks if b.kind is BlockKind.TRABAJO)
    reader.items = (busy_from(first.interval, "google:primary"),)
    outcome = planner.aprobar(WEEK)
    assert not outcome.accepted
    assert ConflictCode.EVENTO_EXTERNO in {c.code for c in outcome.conflicts}
    assert parse_weekly(vault.read(WEEKLY).text).status is PlanStatus.PROPUESTO


def test_unreachable_calendar_still_proposes_with_an_alert(
    planner: JefePlanner, reader: FakeBusyReader
) -> None:
    reader.error = CalendarError("Google Calendar no respondió (TimeoutError)")
    outcome = planner.proponer(WEEK)
    assert outcome.accepted
    assert any("Google Calendar no disponible" in alert for alert in outcome.proposal.alerts)


def test_unreachable_calendar_blocks_the_approval(
    planner: JefePlanner, reader: FakeBusyReader, audit: MemoryAuditLog
) -> None:
    planner.proponer(WEEK)
    reader.error = CalendarError("Google Calendar no respondió (TimeoutError)")
    with pytest.raises(ApplicationError, match="la aprobación necesita comprobar"):
        planner.aprobar(WEEK)
    assert ("leer_calendario", AuditResult.ERROR) in [(r.action, r.result) for r in audit.records]


def test_without_calendar_nothing_changes(vault: FilesystemVault, write_note: WriteNote) -> None:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    tools = ToolRegistry(vault, samples.effective(), MemoryAuditLog(), FakeClock(NOW))
    outcome = JefePlanner(tools, FakeClock(NOW)).proponer(WEEK)
    assert outcome.accepted
    assert "## Eventos externos\n- Ninguno" in vault.read(WEEKLY).text
