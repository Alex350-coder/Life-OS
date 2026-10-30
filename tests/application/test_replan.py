"""Replanificación del Jefe con historial (F4-T04, ADR-0011, SEC-09).

Un bloque perdido genera una propuesta del Coach; el Jefe propone una revisión
`Semanal/AAAA-Wnn-rN.md` que reubica el trabajo desde ahora, conserva lo ya ocurrido y nunca
reescribe un plan aprobado ni un día pasado. El propietario la aprueba como cualquier plan.
"""

from datetime import date, datetime

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.coach import CoachSession
from lifeos.application.jefe import JefePlanner, PlanAlreadyApprovedError
from lifeos.application.policy_vault import PermissionDeniedError, PolicyVault
from lifeos.application.tasks import ApplicationError
from lifeos.application.tools import ToolRegistry
from lifeos.domain.coach import CheckInState
from lifeos.domain.plan import BlockKind, ConflictCode, PlanStatus
from lifeos.domain.plan_note import parse_daily_blocks, parse_weekly
from lifeos.ports.config import WriteMode
from lifeos.ports.logs import AuditResult
from lifeos.ports.vault import VaultPath
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.fakes import FakeClock, MemoryAuditLog
from tests.planning_builders import LIMA, MON, NOW, TUE, WEEK, at, tid
from tests.planning_samples import HORARIO

pytestmark = [pytest.mark.integration, pytest.mark.security]

WEEKLY = VaultPath.parse("Semanal/2026-W45.md")
R1 = VaultPath.parse("Semanal/2026-W45-r1.md")
R2 = VaultPath.parse("Semanal/2026-W45-r2.md")
MONDAY = VaultPath.parse("Diario/2026-11-02.md")
TUESDAY = VaultPath.parse("Diario/2026-11-03.md")
TASKS = (
    "- [ ] Informe [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 3h] 📅 2026-11-06\n"
    "- [ ] Lectura [id:: t-bbbbbb] [area:: universidad] [esfuerzo:: 1h]\n"
)


class World:
    """Bóveda con un plan aprobado el domingo y un reloj que se puede mover."""

    def __init__(self, vault: FilesystemVault, audit: MemoryAuditLog) -> None:
        self.vault = vault
        self.audit = audit
        self.clock = FakeClock(NOW)
        self.tools = ToolRegistry(vault, samples.effective(), audit, self.clock)
        self.jefe = JefePlanner(self.tools, self.clock)
        self.coach = CoachSession(self.tools, self.clock)

    def at(self, moment: datetime) -> "World":
        self.clock.current = moment
        self.clock.elapsed = 0.0
        return self

    def text(self, path: VaultPath) -> str:
        return self.vault.read(path).text

    def first_work(self, path: VaultPath, day: date) -> tuple[str, str]:
        blocks = parse_daily_blocks(self.text(path), day, LIMA)
        block = next(b for b in blocks if b.kind is BlockKind.TRABAJO)
        return str(block.task_id), f"{block.interval.start.astimezone(LIMA):%H:%M}"


@pytest.fixture
def world(vault: FilesystemVault, write_note: WriteNote) -> World:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    w = World(vault, MemoryAuditLog())
    assert w.jefe.proponer(WEEK).accepted
    assert w.jefe.aprobar(WEEK).accepted
    return w


def _missed_first_monday_block(world: World) -> None:
    task_id, _ = world.first_work(MONDAY, MON)
    world.at(at(MON, "09:45")).coach.checkin("", state=CheckInState.PERDIDO, task_id=tid(task_id))


# --- Propuesta de revisión --------------------------------------------------------------------


def test_replan_proposes_a_revision_without_touching_the_approved_plan(world: World) -> None:
    approved = world.text(WEEKLY)
    _missed_first_monday_block(world)
    monday = world.text(MONDAY)
    outcome = world.at(at(MON, "09:50")).jefe.replanificar(WEEK)
    assert outcome.accepted
    assert outcome.path == R1
    assert outcome.status is PlanStatus.PROPUESTO
    assert world.text(WEEKLY) == approved
    assert world.text(MONDAY) == monday  # una revisión propuesta no publica diarios
    note = parse_weekly(world.text(R1))
    assert note.revision == 1 and note.revises == str(WEEKLY)
    assert "bloque perdido" in world.text(R1)


def test_missed_block_is_relocated_after_now_and_history_is_kept(world: World) -> None:
    previous = parse_weekly(world.text(WEEKLY))
    _missed_first_monday_block(world)
    world.at(at(MON, "09:50")).jefe.replanificar(WEEK)
    note = parse_weekly(world.text(R1))
    cutoff = at(MON, "09:50")
    kept = [b for b in previous.blocks if b.task_id and b.interval.start < cutoff]
    assert list(note.history()) == kept
    future = [b for b in note.plan_blocks() if b.task_id == tid("t-aaaaaa")]
    assert future and all(b.interval.start >= cutoff for b in future)
    assert sum(b.interval.minutes for b in future) == 180  # el esfuerzo restante no cambió


def test_reduce_load_moves_todays_remaining_work_to_later_days(world: World) -> None:
    world.at(at(MON, "09:45")).coach.checkin("Agotado", state=CheckInState.CANSADO)
    world.at(at(MON, "09:50")).jefe.replanificar(WEEK)
    note = parse_weekly(world.text(R1))
    assert all(
        b.interval.start.astimezone(LIMA).date() != MON for b in note.plan_blocks() if b.task_id
    )


def test_replan_requires_an_approved_plan(vault: FilesystemVault, write_note: WriteNote) -> None:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    w = World(vault, MemoryAuditLog())
    with pytest.raises(ApplicationError, match="aprobado"):
        w.jefe.replanificar(WEEK)
    w.jefe.proponer(WEEK)
    with pytest.raises(ApplicationError, match="aprobado"):
        w.jefe.replanificar(WEEK)


def test_proposing_again_on_an_approved_week_points_to_replanning(world: World) -> None:
    with pytest.raises(PlanAlreadyApprovedError, match="--replanificar"):
        world.at(at(MON, "09:50")).jefe.proponer(WEEK)


def test_replan_after_the_week_ended_is_rejected(world: World) -> None:
    with pytest.raises(ApplicationError, match="terminó"):
        world.at(datetime(2026, 11, 9, 9, 0, tzinfo=LIMA)).jefe.replanificar(WEEK)


def test_proposed_revision_is_replaced_by_a_newer_proposal(world: World) -> None:
    world.at(at(MON, "09:50")).jefe.replanificar(WEEK)
    first = world.text(R1)
    world.at(at(MON, "10:30")).jefe.replanificar(WEEK)
    assert world.text(R1) != first
    assert not world.vault.exists(R2)


# --- Aprobación de la revisión ----------------------------------------------------------------


def test_approving_a_revision_republishes_only_today_and_later(world: World) -> None:
    sunday_week = world.text(WEEKLY)
    _missed_first_monday_block(world)
    world.at(at(MON, "09:50")).jefe.replanificar(WEEK)
    outcome = world.at(at(MON, "09:55")).jefe.aprobar(WEEK)
    assert outcome.accepted and outcome.status is PlanStatus.APROBADO
    assert outcome.path == R1
    assert parse_weekly(world.text(R1)).status is PlanStatus.APROBADO
    assert world.text(WEEKLY) == sunday_week
    monday = world.text(MONDAY)
    assert "ajuste: bloque perdido" in monday  # los check-ins se conservan
    blocks = parse_daily_blocks(monday, MON, LIMA)
    early = [b for b in blocks if b.task_id and b.interval.start < at(MON, "09:50")]
    assert early  # el bloque perdido sigue en el día como historial


def test_past_days_are_never_rewritten(world: World) -> None:
    world.at(at(TUE, "09:00")).jefe.replanificar(WEEK)
    monday = world.text(MONDAY)
    world.at(at(TUE, "09:05")).jefe.aprobar(WEEK)
    assert world.text(MONDAY) == monday


def test_second_revision_keeps_the_first(world: World) -> None:
    world.at(at(MON, "09:50")).jefe.replanificar(WEEK)
    world.at(at(MON, "09:55")).jefe.aprobar(WEEK)
    r1 = world.text(R1)
    outcome = world.at(at(TUE, "09:00")).jefe.replanificar(WEEK)
    assert outcome.path == R2
    assert parse_weekly(world.text(R2)).revises == str(R1)
    world.at(at(TUE, "09:05")).jefe.aprobar(WEEK)
    assert world.text(R1) == r1


def test_edited_history_blocks_the_approval(world: World) -> None:
    world.at(at(MON, "09:50")).jefe.replanificar(WEEK)
    _, start = world.first_work(MONDAY, MON)
    text = world.text(R1)
    edited = text.replace(f"- {start}–", "- 07:00–", 1)
    world.vault.write(R1, edited, expected=world.vault.read(R1).version)
    monday = world.text(MONDAY)
    outcome = world.at(at(MON, "09:55")).jefe.aprobar(WEEK)
    assert not outcome.accepted
    assert ConflictCode.HISTORIAL_ALTERADO in {c.code for c in outcome.conflicts}
    assert world.text(MONDAY) == monday
    assert parse_weekly(world.text(R1)).status is PlanStatus.PROPUESTO


def test_revision_approved_too_late_is_rejected(world: World) -> None:
    world.at(at(MON, "09:50")).jefe.replanificar(WEEK)
    outcome = world.at(at(MON, "19:00")).jefe.aprobar(WEEK)
    assert not outcome.accepted
    assert ConflictCode.FECHA_PASADA in {c.code for c in outcome.conflicts}


# --- SEC-09: planes aprobados inmutables y nombres canónicos ----------------------------------


def test_approved_weekly_note_cannot_be_overwritten(world: World) -> None:
    scoped = PolicyVault(world.vault, samples.effective()["jefe"], WriteMode.PLAN)
    snapshot = world.vault.read(WEEKLY)
    with pytest.raises(PermissionDeniedError) as denied:
        scoped.write(WEEKLY, snapshot.text + "\n", expected=snapshot.version)
    assert denied.value.reason == "plan_aprobado_inmutable"


@pytest.mark.parametrize("name", ["2026-W45-r0.md", "2026-W45-r21.md", "2026-W45-rx.md"])
def test_non_canonical_revision_names_are_rejected(world: World, name: str) -> None:
    scoped = PolicyVault(world.vault, samples.effective()["jefe"], WriteMode.PLAN)
    with pytest.raises(PermissionDeniedError) as denied:
        scoped.write(VaultPath.parse(f"Semanal/{name}"), "x", expected=None)
    assert denied.value.reason == "nombre_de_plan_invalido"


def test_coach_cannot_publish_a_revision(world: World) -> None:
    world.at(at(MON, "09:50")).jefe.replanificar(WEEK)
    scoped = PolicyVault(world.vault, samples.effective()["coach"], WriteMode.PLAN)
    with pytest.raises(PermissionDeniedError):
        scoped.write(R2, world.text(R1), expected=None)


def test_replan_is_audited_with_the_revision_path(world: World) -> None:
    world.at(at(MON, "09:50")).jefe.replanificar(WEEK)
    assert ("jefe", "publicar_plan", str(R1), AuditResult.OK) in [
        (r.actor, r.action, r.target, r.result) for r in world.audit.records
    ]
