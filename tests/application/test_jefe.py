"""Caso de uso del Jefe: propuesta → validación → aprobación → publicación (F3-T03, SEC-09)."""

from dataclasses import replace

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.jefe import (
    JefePlanner,
    PlanAlreadyApprovedError,
    PlanNotFoundError,
)
from lifeos.application.plan_publication import (
    PlanPublication,
    PlanRejectedError,
    PublicationMismatchError,
)
from lifeos.application.policy_vault import PermissionDeniedError
from lifeos.application.tasks import ApplicationError
from lifeos.application.tools import ActorContext, ToolRegistry
from lifeos.domain.horario_note import HorarioIncompletoError
from lifeos.domain.plan import (
    Block,
    BlockKind,
    ConflictCode,
    PlanningContext,
    PlanOrigin,
    PlanProposal,
    PlanStatus,
    fixed_and_protected_blocks,
)
from lifeos.domain.plan_note import parse_weekly
from lifeos.ports.logs import AuditResult
from lifeos.ports.vault import VaultPath
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.fakes import FakeClock, MemoryAuditLog
from tests.planning_builders import MON, NOW, WEEK, span, tid, work
from tests.planning_samples import HORARIO, HORARIO_SIN_LIMITES

pytestmark = [pytest.mark.integration, pytest.mark.security]

WEEKLY = VaultPath.parse("Semanal/2026-W45.md")
MONDAY_NOTE = VaultPath.parse("Diario/2026-11-02.md")
TASKS = (
    "- [ ] Informe [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 3h] 📅 2026-11-04\n"
    "- [ ] Lectura [id:: t-bbbbbb] [area:: universidad] [esfuerzo:: 1h]\n"
)
COACH = ActorContext(agent_id="coach", correlation_id="c-coach")


class OverlappingDrafter:
    """Doble de prueba: propone dos bloques que se solapan (como un modelo descuidado)."""

    def draft(self, context: PlanningContext) -> PlanProposal:
        return PlanProposal(
            week=context.week,
            blocks=(
                *fixed_and_protected_blocks(context),
                work(MON, "08:00–09:30", "t-aaaaaa"),
                work(MON, "09:00–10:00", "t-bbbbbb"),
            ),
            origin=PlanOrigin.IA,
        )


@pytest.fixture
def audit() -> MemoryAuditLog:
    return MemoryAuditLog()


@pytest.fixture
def tools(vault: FilesystemVault, audit: MemoryAuditLog) -> ToolRegistry:
    return ToolRegistry(vault, samples.effective(), audit, FakeClock(NOW))


@pytest.fixture
def planner(tools: ToolRegistry, write_note: WriteNote) -> JefePlanner:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    return JefePlanner(tools, FakeClock(NOW), drafter=OverlappingDrafter())


def _actions(audit: MemoryAuditLog) -> list[tuple[str, str, AuditResult, str | None]]:
    return [(r.actor, r.action, r.result, r.reason) for r in audit.records]


# --- Propuesta --------------------------------------------------------------------------------


def test_valid_proposal_is_saved_as_proposed(
    planner: JefePlanner, vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    outcome = planner.proponer(WEEK)
    assert outcome.accepted
    assert outcome.conflicts == ()
    assert outcome.path == WEEKLY
    note = parse_weekly(vault.read(WEEKLY).text)
    assert note.status is PlanStatus.PROPUESTO
    assert {b.task_id for b in note.blocks if b.task_id} == {tid("t-aaaaaa"), tid("t-bbbbbb")}
    assert ("jefe", "publicar_plan", AuditResult.OK, None) in _actions(audit)


def test_default_week_is_the_current_week_in_the_schedule_zone(planner: JefePlanner) -> None:
    # NOW es domingo 2026-11-01 (semana 44): la propuesta por defecto es de esa semana.
    assert planner.proponer().proposal.week.render() == "2026-W44"


def test_invalid_plan_is_rejected_and_never_saved(
    planner: JefePlanner, vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    outcome = planner.proponer(WEEK, origin=PlanOrigin.IA)
    assert not outcome.accepted
    assert ConflictCode.SOLAPAMIENTO in {conflict.code for conflict in outcome.conflicts}
    assert outcome.path is None
    assert not vault.exists(WEEKLY)
    assert ("jefe", "publicar_plan", AuditResult.RECHAZADO, "plan_invalido") in _actions(audit)


def test_unapproved_plan_stays_proposed(planner: JefePlanner, vault: FilesystemVault) -> None:
    planner.proponer(WEEK)
    planner.proponer(WEEK)  # volver a proponer reemplaza la propuesta anterior
    assert parse_weekly(vault.read(WEEKLY).text).status is PlanStatus.PROPUESTO
    assert not vault.exists(MONDAY_NOTE)


def test_tasks_in_restricted_or_archived_areas_are_not_planned(
    planner: JefePlanner, write_note: WriteNote
) -> None:
    write_note(
        "05-Salud/medico.md",
        "- [ ] Cita [id:: t-cccccc] [area:: salud] [esfuerzo:: 1h]\n",
    )
    write_note(
        "Archivo/01-Universidad/viejo.md",
        "- [ ] Vieja [id:: t-dddddd] [area:: universidad] [esfuerzo:: 1h]\n",
    )
    planned = {b.task_id for b in planner.proponer(WEEK).proposal.work_blocks()}
    assert planned == {tid("t-aaaaaa"), tid("t-bbbbbb")}


def test_missing_schedule_is_a_clear_error(tools: ToolRegistry) -> None:
    with pytest.raises(ApplicationError, match="horario"):
        JefePlanner(tools, FakeClock(NOW)).proponer(WEEK)


def test_incomplete_limits_stop_planning(tools: ToolRegistry, write_note: WriteNote) -> None:
    write_note("_sistema/horario.md", HORARIO_SIN_LIMITES)
    with pytest.raises(HorarioIncompletoError):
        JefePlanner(tools, FakeClock(NOW)).proponer(WEEK)


def test_configured_zone_must_match_the_schedule(
    tools: ToolRegistry, write_note: WriteNote
) -> None:
    write_note("_sistema/horario.md", HORARIO)
    planner = JefePlanner(tools, FakeClock(NOW), configured_zone="Europe/Madrid")
    with pytest.raises(ApplicationError, match="LIFEOS_TIMEZONE"):
        planner.proponer(WEEK)


def test_ai_origin_without_a_drafter_is_an_error(
    tools: ToolRegistry, write_note: WriteNote
) -> None:
    write_note("_sistema/horario.md", HORARIO)
    with pytest.raises(ApplicationError, match="IA"):
        JefePlanner(tools, FakeClock(NOW)).proponer(WEEK, origin=PlanOrigin.IA)


# --- Aprobación y publicación -----------------------------------------------------------------


def test_approval_publishes_weekly_and_daily_notes(
    planner: JefePlanner, vault: FilesystemVault
) -> None:
    planner.proponer(WEEK)
    outcome = planner.aprobar(WEEK)
    assert outcome.accepted
    assert outcome.status is PlanStatus.APROBADO
    assert parse_weekly(vault.read(WEEKLY).text).status is PlanStatus.APROBADO
    monday = vault.read(MONDAY_NOTE).text
    assert "estado_plan: aprobado" in monday
    assert "[id:: t-aaaaaa]" in monday
    assert vault.exists(VaultPath.parse("Diario/2026-11-08.md"))  # domingo: descanso protegido


def test_approval_keeps_existing_checkins(
    planner: JefePlanner, vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(
        "Diario/2026-11-02.md",
        "---\ntipo: diario\n---\n\n## Check-ins\n- 07:30 · llegué temprano\n\n## Cierre\n",
    )
    planner.proponer(WEEK)
    planner.aprobar(WEEK)
    assert "- 07:30 · llegué temprano\n" in vault.read(MONDAY_NOTE).text


def test_approval_without_a_proposal_fails(planner: JefePlanner) -> None:
    with pytest.raises(PlanNotFoundError):
        planner.aprobar(WEEK)


def test_approved_plan_cannot_be_approved_or_replaced_again(planner: JefePlanner) -> None:
    planner.proponer(WEEK)
    planner.aprobar(WEEK)
    with pytest.raises(PlanAlreadyApprovedError):
        planner.aprobar(WEEK)
    with pytest.raises(PlanAlreadyApprovedError):
        planner.proponer(WEEK)


def test_hand_edited_invalid_plan_is_rejected_at_approval(
    planner: JefePlanner, vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    planner.proponer(WEEK)
    snapshot = vault.read(WEEKLY)
    edited = snapshot.text.replace(
        "### mar 2026-11-03\n",
        "### mar 2026-11-03\n- 18:00–19:00 · Invade el gym [id:: t-bbbbbb]\n",
    )
    vault.write(WEEKLY, edited, expected=snapshot.version)
    outcome = planner.aprobar(WEEK)
    assert not outcome.accepted
    assert ConflictCode.TIEMPO_PROTEGIDO in {c.code for c in outcome.conflicts}
    assert parse_weekly(vault.read(WEEKLY).text).status is PlanStatus.PROPUESTO
    assert not vault.exists(MONDAY_NOTE)


def test_malformed_plan_note_fails_closed_at_approval(
    planner: JefePlanner, vault: FilesystemVault
) -> None:
    planner.proponer(WEEK)
    snapshot = vault.read(WEEKLY)
    vault.write(WEEKLY, snapshot.text.replace("08:00–", "8h–", 1), expected=snapshot.version)
    with pytest.raises(ApplicationError):
        planner.aprobar(WEEK)
    assert not vault.exists(MONDAY_NOTE)


# --- SEC-09: la publicación solo la hace el Jefe con un plan validado -------------------------


def _publication(planner: JefePlanner) -> PlanPublication:
    context = planner.context(WEEK)
    proposal = PlanProposal(week=WEEK, blocks=fixed_and_protected_blocks(context))
    return PlanPublication(
        proposal=proposal, context=context, status=PlanStatus.PROPUESTO, generated=NOW
    )


def test_coach_cannot_publish_a_plan(
    planner: JefePlanner, tools: ToolRegistry, vault: FilesystemVault, audit: MemoryAuditLog
) -> None:
    with pytest.raises(PermissionDeniedError) as info:
        tools.publicar_plan(COACH, _publication(planner))
    assert info.value.reason == "herramienta_no_autorizada"
    assert not vault.exists(WEEKLY)
    assert ("coach", "publicar_plan", AuditResult.RECHAZADO, "herramienta_no_autorizada") in (
        _actions(audit)
    )


def test_the_tool_renders_the_text_itself(
    planner: JefePlanner, tools: ToolRegistry, vault: FilesystemVault
) -> None:
    # El llamador no aporta texto: lo que se escribe es siempre el render del plan validado.
    paths = tools.publicar_plan(planner.new_actor(), _publication(planner))
    assert paths == (WEEKLY,)
    note = parse_weekly(vault.read(WEEKLY).text)
    assert note.status is PlanStatus.PROPUESTO


def test_approved_publication_requires_the_approval_time(
    planner: JefePlanner, tools: ToolRegistry, vault: FilesystemVault
) -> None:
    approved = replace(_publication(planner), status=PlanStatus.APROBADO)
    with pytest.raises(PublicationMismatchError):
        tools.publicar_plan(planner.new_actor(), approved)
    assert not vault.exists(WEEKLY)
    assert not vault.exists(MONDAY_NOTE)


def test_tool_validates_even_when_called_directly(
    planner: JefePlanner, tools: ToolRegistry, vault: FilesystemVault
) -> None:
    context = planner.context(WEEK)
    bad = PlanProposal(
        week=WEEK,
        blocks=(
            Block(BlockKind.TRABAJO, "Fuera de horario", span(MON, "21:00–22:00"), tid("t-bbbbbb")),
        ),
    )
    with pytest.raises(PlanRejectedError) as info:
        tools.publicar_plan(
            planner.new_actor(),
            PlanPublication(
                proposal=bad, context=context, status=PlanStatus.PROPUESTO, generated=NOW
            ),
        )
    assert ConflictCode.FIJO_ALTERADO in {c.code for c in info.value.conflicts}
    assert not vault.exists(WEEKLY)
