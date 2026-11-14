"""Herramientas del calendario: permisos en código y auditoría de cada operación externa
(ADR-0017, SEC-04, SEC-13, RULES E.7 y G.5)."""

import pytest

from lifeos.application.calendar_tools import CalendarTools, PartialCalendarSyncError
from lifeos.application.policy_vault import PermissionDeniedError
from lifeos.application.tools import ActorContext
from lifeos.domain.calendar_sync import desired_entries, plan_sync
from lifeos.domain.external import busy_from
from lifeos.ports.calendar import CalendarError
from lifeos.ports.logs import AuditResult
from tests import config_samples as samples
from tests.calendar_fakes import FakeBusyReader, FakeCalendarWriter
from tests.fakes import FakeClock, MemoryAuditLog
from tests.planning_builders import MON, NOW, TUE, WEEK, at, span, work

pytestmark = [pytest.mark.unit, pytest.mark.security]
JEFE = ActorContext("jefe", "c-jefe")
COACH = ActorContext("coach", "c-coach")
BLOCKS = (work(MON, "14:00–15:00", "t-aaaaaa"), work(TUE, "09:00–10:00", "t-bbbbbb"))
START, END = at(MON, "00:00"), at(TUE, "23:00")


def make(
    reader: FakeBusyReader | None = None, writer: FakeCalendarWriter | None = None
) -> tuple[CalendarTools, MemoryAuditLog]:
    audit = MemoryAuditLog()
    tools = CalendarTools(samples.effective(), audit, FakeClock(NOW), reader=reader, writer=writer)
    return tools, audit


def results(audit: MemoryAuditLog) -> list[tuple[str, str, AuditResult]]:
    return [(r.actor, r.action, r.result) for r in audit.records]


def test_jefe_reads_busy_intervals() -> None:
    item = busy_from(span(MON, "14:00–15:00"), "google:primary")
    tools, _ = make(reader=FakeBusyReader((item,)))
    assert tools.busy(JEFE, START, END) == (item,)


def test_without_reader_there_is_nothing_to_read() -> None:
    tools, _ = make()
    assert not tools.can_read
    assert tools.busy(JEFE, START, END) == ()


def test_coach_cannot_read_the_calendar_and_it_is_audited() -> None:
    reader = FakeBusyReader()
    tools, audit = make(reader=reader)
    with pytest.raises(PermissionDeniedError):
        tools.busy(COACH, START, END)
    assert reader.calls == []
    assert results(audit) == [("coach", "leer_calendario", AuditResult.RECHAZADO)]


def test_read_error_is_audited_and_propagated() -> None:
    tools, audit = make(reader=FakeBusyReader(error=CalendarError("sin red")))
    with pytest.raises(CalendarError):
        tools.busy(JEFE, START, END)
    assert results(audit) == [("jefe", "leer_calendario", AuditResult.ERROR)]


def test_coach_cannot_publish_and_nothing_is_written() -> None:
    writer = FakeCalendarWriter()
    tools, audit = make(writer=writer)
    sync = plan_sync(desired_entries(WEEK, BLOCKS), ())
    with pytest.raises(PermissionDeniedError):
        tools.apply(COACH, WEEK.render(), sync)
    assert writer.events == {}
    assert results(audit) == [("coach", "publicar_calendario", AuditResult.RECHAZADO)]


def test_apply_audits_attempt_and_result_of_every_operation() -> None:
    writer = FakeCalendarWriter()
    tools, audit = make(writer=writer)
    applied = tools.apply(JEFE, WEEK.render(), plan_sync(desired_entries(WEEK, BLOCKS), ()))
    assert (applied.created, applied.updated, applied.deleted) == (2, 0, 0)
    assert (
        results(audit)
        == [
            ("jefe", "publicar_calendario", AuditResult.INTENTO),
            ("jefe", "publicar_calendario", AuditResult.OK),
        ]
        * 2
    )
    # El destino identifica el evento por su clave, nunca por el título de la tarea.
    assert all(r.target.startswith(f"calendario:{WEEK.render()}:") for r in audit.records)
    assert all("Trabajo" not in r.target for r in audit.records)


def test_failure_halfway_reports_what_was_done() -> None:
    writer = FakeCalendarWriter(fail_after=1)
    tools, audit = make(writer=writer)
    with pytest.raises(PartialCalendarSyncError) as error:
        tools.apply(JEFE, WEEK.render(), plan_sync(desired_entries(WEEK, BLOCKS), ()))
    assert error.value.done == 1
    assert "repite" in str(error.value)
    assert results(audit)[-1] == ("jefe", "publicar_calendario", AuditResult.ERROR)


def test_no_attempt_audit_means_no_external_write() -> None:
    writer = FakeCalendarWriter()
    tools, audit = make(writer=writer)
    audit.fail_on = AuditResult.INTENTO
    with pytest.raises(Exception):  # noqa: B017 - el error de log que corresponda
        tools.apply(JEFE, WEEK.render(), plan_sync(desired_entries(WEEK, BLOCKS), ()))
    assert writer.events == {}


def test_published_lists_only_with_the_publish_permission() -> None:
    writer = FakeCalendarWriter()
    tools, _ = make(writer=writer)
    assert tools.published(JEFE, WEEK.render(), START, END) == ()
    with pytest.raises(PermissionDeniedError):
        tools.published(COACH, WEEK.render(), START, END)


def test_publishing_without_writer_is_a_clear_error() -> None:
    tools, _ = make()
    assert not tools.can_write
    with pytest.raises(CalendarError, match="lifeos-calendario conectar --escritura"):
        tools.published(JEFE, WEEK.render(), START, END)
