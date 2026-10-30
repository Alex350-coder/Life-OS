"""Vista de la semana del Jefe (F5-T02): último plan de la cadena y carga por día, sin escribir.

La web la muestra tal cual; leerla no cambia la bóveda ni deja escrituras en la auditoría.
"""

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.coach import CoachSession
from lifeos.application.jefe import JefePlanner
from lifeos.application.tasks import ApplicationError
from lifeos.application.tools import ToolRegistry
from lifeos.domain.capacity import week_capacity
from lifeos.domain.coach import CheckInState
from lifeos.domain.horario_note import parse_horario
from lifeos.domain.load import day_loads
from lifeos.domain.plan import BlockKind, PlanOrigin, PlanStatus
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.fakes import FakeClock, MemoryAuditLog
from tests.planning_builders import LIMA, MON, NOW, WED, WEEK, at
from tests.planning_samples import HORARIO

pytestmark = pytest.mark.integration

TASKS = (
    "- [ ] Informe [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 3h] 📅 2026-11-06\n"
    "- [ ] Lectura [id:: t-bbbbbb] [area:: universidad] [esfuerzo:: 1h]\n"
)


def _snapshot(vault: FilesystemVault) -> dict[str, str]:
    return {str(p): vault.read(p).text for p in vault.list_notes()}


@pytest.fixture
def audit() -> MemoryAuditLog:
    return MemoryAuditLog()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def jefe(vault: FilesystemVault, audit: MemoryAuditLog, clock: FakeClock) -> JefePlanner:
    return JefePlanner(ToolRegistry(vault, samples.effective(), audit, clock), clock)


@pytest.fixture
def ready(write_note: WriteNote) -> None:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)


@pytest.mark.usefixtures("ready")
def test_week_without_plan_shows_capacity_only(jefe: JefePlanner) -> None:
    view = jefe.semana(WEEK)
    assert view.week == WEEK
    assert view.status is None and view.path is None and view.versions == 0
    assert view.blocks == ()
    assert len(view.load) == 7
    assert all(load.planned_minutes == 0 for load in view.load)
    assert view.load[0].fixed_minutes == 120


@pytest.mark.usefixtures("ready")
def test_week_with_proposal_shows_its_blocks_and_load(jefe: JefePlanner) -> None:
    proposed = jefe.proponer(WEEK)
    view = jefe.semana(WEEK)
    assert view.status is PlanStatus.PROPUESTO
    assert view.origin is PlanOrigin.HEURISTICA
    assert view.path == "Semanal/2026-W45.md" == str(proposed.path)
    assert view.revision == 0 and view.versions == 1
    work = [b for b in view.blocks if b.kind is BlockKind.TRABAJO]
    assert {b.task_id for b in work} == {b.task_id for b in proposed.proposal.work_blocks()}
    capacity = week_capacity(parse_horario(HORARIO), WEEK)
    assert view.load == day_loads(capacity, view.blocks, LIMA)
    planned = sum(load.planned_minutes for load in view.load)
    assert planned == sum(b.interval.minutes for b in work) > 0


@pytest.mark.usefixtures("ready")
def test_week_shows_the_latest_revision(
    jefe: JefePlanner, vault: FilesystemVault, audit: MemoryAuditLog, clock: FakeClock
) -> None:
    jefe.proponer(WEEK)
    jefe.aprobar(WEEK)
    coach = CoachSession(ToolRegistry(vault, samples.effective(), audit, clock), clock)
    clock.current, clock.elapsed = at(MON, "08:30"), 0.0
    coach.checkin("", state=CheckInState.CANSADO)
    jefe.replanificar(WEEK)
    view = jefe.semana(WEEK)
    assert view.path == "Semanal/2026-W45-r1.md"
    assert view.revision == 1 and view.versions == 2
    assert view.status is PlanStatus.PROPUESTO


@pytest.mark.usefixtures("ready")
def test_reading_the_week_changes_nothing(
    jefe: JefePlanner, vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    jefe.proponer(WEEK)
    before, records = _snapshot(vault), len(audit.records)
    jefe.semana(WEEK)
    assert _snapshot(vault) == before
    assert audit.records[records:] == []


def test_week_without_horario_is_an_error(jefe: JefePlanner) -> None:
    with pytest.raises(ApplicationError, match="D-13"):
        jefe.semana(WEEK)


@pytest.mark.usefixtures("ready")
def test_week_with_a_gap_in_the_chain_is_an_error(jefe: JefePlanner, write_note: WriteNote) -> None:
    jefe.proponer(WEEK)
    write_note("Semanal/2026-W45-r2.md", "---\ntipo: semanal\n---\n")
    with pytest.raises(ApplicationError, match=r"falta Semanal/2026-W45-r1.md"):
        jefe.semana(WEEK)


@pytest.mark.usefixtures("ready")
def test_week_load_covers_the_whole_week_even_mid_week(jefe: JefePlanner, clock: FakeClock) -> None:
    clock.current, clock.elapsed = at(WED, "12:00"), 0.0
    view = jefe.semana(WEEK)
    capacity = week_capacity(parse_horario(HORARIO), WEEK)
    assert [load.budget_minutes for load in view.load] == [c.budget_minutes for c in capacity]


@pytest.mark.usefixtures("ready")
def test_week_shows_an_approved_plan(jefe: JefePlanner) -> None:
    jefe.proponer(WEEK)
    jefe.aprobar(WEEK)
    view = jefe.semana(WEEK)
    assert view.status is PlanStatus.APROBADO and view.revision == 0
