"""Regresiones de la revisión de la Fase 4 (agent-evaluator, security-reviewer, python-reviewer,
code-reviewer y silent-failure-hunter). Cada prueba fija un hallazgo corregido."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.jsonl_logs import JsonlUsageLog, JsonlUsageReader
from lifeos.adapters.schemas import SCHEMAS
from lifeos.adapters.yaml_config import parse_catalog
from lifeos.application.closing import DailyClosing
from lifeos.application.coach import CoachSession
from lifeos.application.coach_ai import SYSTEM_PROMPT, AiCoachWriter
from lifeos.application.jefe import JefePlanner
from lifeos.application.llm_calls import ModelGateway
from lifeos.application.tasks import ApplicationError
from lifeos.application.tools import ToolRegistry
from lifeos.application.usage_report import summarize
from lifeos.cli.main import _answers, _printable, build_parser
from lifeos.domain.adjustments import parse_adjustments
from lifeos.domain.closing import AnswerKind, ClosingAnswer
from lifeos.domain.coach import CheckInState, ReplyKind, decide, micro_step_for
from lifeos.domain.errors import ValidationError
from lifeos.domain.plan_note import parse_daily_blocks, parse_weekly
from lifeos.ports.logs import CostStatus, UsageScan
from lifeos.ports.vault import VaultPath
from tests import config_samples as samples
from tests.adapters.test_usage_reader import record
from tests.application.conftest import WriteNote
from tests.daily_builders import TASKS, monday_note
from tests.fakes import FakeClock, FakeLlmClient, MemoryAuditLog, MemoryUsageLog, ok_response
from tests.planning_builders import LIMA, MON, NOW, WEEK, at, tid
from tests.planning_samples import HORARIO

pytestmark = [pytest.mark.integration, pytest.mark.security]

MONDAY = VaultPath.parse("Diario/2026-11-02.md")
INVISIBLE = ("\u202e", "\u2066", "\u200b", "\x1b", "\x07", "\u2028")


@pytest.fixture
def seeded(write_note: WriteNote) -> None:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    write_note(str(MONDAY), monday_note())


def _ai_session(
    vault: FilesystemVault, reply: str, *, usage: MemoryUsageLog | None = None
) -> CoachSession:
    clock = FakeClock(at(MON, "09:45"))
    client = FakeLlmClient(script=[ok_response(reply)], clock=clock)
    gateway = ModelGateway(
        {"nvidia": client},
        parse_catalog(samples.dump(samples.catalog()).encode()),
        clock,
        usage or MemoryUsageLog(),
    )
    tools = ToolRegistry(vault, samples.effective(), MemoryAuditLog(), clock)
    writer = AiCoachWriter(gateway, SCHEMAS["coach_v1"], profile="ligero")
    return CoachSession(tools, clock, writer=writer)


def _checkins(vault: FilesystemVault) -> list[str]:
    body = vault.read(MONDAY).text.split("## Check-ins", 1)[1].split("## Cierre", 1)[0]
    return [line for line in body.splitlines() if line.startswith("- ")]


# --- agent-evaluator --------------------------------------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_usage_log_failure_after_the_checkin_falls_back_instead_of_failing(
    vault: FilesystemVault,
) -> None:
    # Antes: UsageNotRecordedError salía tras anexar el check-in e invitaba a repetirlo.
    session = _ai_session(vault, json.dumps({"mensaje": "Ok"}), usage=MemoryUsageLog(fail=True))
    outcome = session.checkin("Hola", use_ai=True)
    assert outcome.ai_fallback
    assert len(_checkins(vault)) == 1


@pytest.mark.parametrize("char", INVISIBLE)
def test_model_text_cannot_carry_control_or_bidi_characters(char: str) -> None:
    raw = json.dumps(
        {"mensaje": f"Hola{char}mundo", "micro_paso": {"accion": f"Abre{char}PDF", "minutos": 5}}
    )
    draft = SCHEMAS["coach_v1"].parse(raw)
    assert char not in draft.message
    assert draft.micro_step is not None and char not in draft.micro_step.action


@pytest.mark.usefixtures("seeded")
def test_ai_text_keeps_the_code_generated_status_for_escalations(vault: FilesystemVault) -> None:
    session = _ai_session(vault, json.dumps({"mensaje": "Ya lo marqué como hecho"}))
    outcome = session.checkin("", state=CheckInState.PERDIDO, task_id=tid("t-aaaaaa"), use_ai=True)
    assert outcome.kind is ReplyKind.REPLANIFICAR
    assert "--replanificar" in outcome.message


def test_coach_contract_allows_omitting_the_micro_step_and_prompt_states_limits() -> None:
    assert SCHEMAS["coach_v1"].parse('{"mensaje": "hola"}').micro_step is None
    assert "280" in SYSTEM_PROMPT and "120" in SYSTEM_PROMPT


def test_decision_carries_the_cleaned_note_explicitly() -> None:
    blocks = parse_daily_blocks(monday_note(), MON, LIMA)
    decision = decide(
        CheckInState.NOTA, now=at(MON, "11:00"), zone=LIMA, blocks=blocks, note="  a  b "
    )
    assert decision.note == "a b"


# --- security-reviewer ------------------------------------------------------------------------


@pytest.mark.parametrize("char", INVISIBLE)
def test_cli_output_strips_format_and_separator_characters(char: str) -> None:
    assert char not in _printable(f"tarea{char}hecha")


@pytest.mark.parametrize("cost", ["NaN", "sNaN", "Infinity", "-1", "1E+999999999"])
def test_non_finite_or_negative_costs_are_malformed(tmp_path: Path, cost: str) -> None:
    JsonlUsageLog(tmp_path).append(
        record(cost_usd=Decimal("0.01"), cost_status=CostStatus.ESTIMADO)
    )
    path = tmp_path / "usage-2026-11.jsonl"
    good = path.read_text(encoding="utf-8").splitlines()[0]
    path.write_text(good.replace('"0.01"', f'"{cost}"') + "\n", encoding="utf-8")
    scan = JsonlUsageReader(tmp_path).read_month(2026, 11)
    assert scan.malformed == 1
    summarize(scan)  # no revienta


@pytest.mark.usefixtures("seeded")
def test_repeating_the_closing_does_not_duplicate_relocation_proposals(
    vault: FilesystemVault,
) -> None:
    for minute in ("21:00", "21:05"):
        clock = FakeClock(at(MON, minute))
        audit = MemoryAuditLog()
        tools = ToolRegistry(vault, samples.effective(), audit, clock)
        closing = DailyClosing(vault, CoachSession(tools, clock), clock, audit)
        closing.apply((ClosingAnswer(tid("t-cccccc"), AnswerKind.NO),))
    assert len(parse_adjustments(vault.read(MONDAY).text, MON)) == 1


# --- python-reviewer --------------------------------------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_briefing_detects_approval_from_the_frontmatter(vault: FilesystemVault) -> None:
    extra = "".join(f"campo{i}: x\n" for i in range(10))
    note = monday_note().replace("---\ntipo: diario\n", f"---\ntipo: diario\n{extra}", 1)
    vault.write(MONDAY, note, expected=vault.read(MONDAY).version)
    clock = FakeClock(at(MON, "08:30"))
    tools = ToolRegistry(vault, samples.effective(), MemoryAuditLog(), clock)
    assert CoachSession(tools, clock).briefing().approved


def test_fallback_uses_exactly_the_deterministic_micro_step() -> None:
    blocks = parse_daily_blocks(monday_note(), MON, LIMA)
    decision = decide(CheckInState.BLOQUEADO, now=at(MON, "08:10"), zone=LIMA, blocks=blocks)
    assert decision.micro_step == micro_step_for(blocks[1])


# --- silent-failure-hunter --------------------------------------------------------------------


@pytest.mark.usefixtures("seeded")
def test_closing_twice_the_same_day_is_refused_without_writing(vault: FilesystemVault) -> None:
    clock = FakeClock(at(MON, "21:00"))
    audit = MemoryAuditLog()
    tools = ToolRegistry(vault, samples.effective(), audit, clock)
    closing = DailyClosing(vault, CoachSession(tools, clock), clock, audit)
    closing.apply((ClosingAnswer(tid("t-aaaaaa"), AnswerKind.HECHA),))
    before = vault.read(MONDAY).text
    with pytest.raises(ApplicationError, match="ya"):
        closing.apply((ClosingAnswer(tid("t-bbbbbb"), AnswerKind.HECHA),))
    assert vault.read(MONDAY).text == before


@pytest.mark.usefixtures("seeded")
def test_closing_only_proposes_blocks_that_already_started(vault: FilesystemVault) -> None:
    clock = FakeClock(at(MON, "15:30"))
    audit = MemoryAuditLog()
    tools = ToolRegistry(vault, samples.effective(), audit, clock)
    closing = DailyClosing(vault, CoachSession(tools, clock), clock, audit)
    preview = closing.preview((ClosingAnswer(tid("t-cccccc"), AnswerKind.NO),))
    assert [b.task_id for b in preview.missed] == [tid("t-cccccc")]
    later = closing.preview((ClosingAnswer(tid("t-bbbbbb"), AnswerKind.NO),))
    assert [b.task_id for b in later.missed] == [tid("t-bbbbbb")]
    clock.current = at(MON, "12:00")
    early = closing.preview((ClosingAnswer(tid("t-cccccc"), AnswerKind.NO),))
    assert early.missed == ()  # 15:00–17:00 aún no empezó


@pytest.mark.usefixtures("seeded")
def test_unreadable_daily_note_is_reported_on_checkin(vault: FilesystemVault) -> None:
    broken = monday_note().replace("· Leer capítulo [id:: t-bbbbbb]", "· Leer capítulo")
    vault.write(MONDAY, broken, expected=vault.read(MONDAY).version)
    clock = FakeClock(at(MON, "08:10"))
    tools = ToolRegistry(vault, samples.effective(), MemoryAuditLog(), clock)
    outcome = CoachSession(tools, clock).checkin("", state=CheckInState.BLOQUEADO)
    assert not outcome.readable


@pytest.mark.usefixtures("seeded")
def test_rejected_adjustment_lines_are_counted_in_the_briefing(vault: FilesystemVault) -> None:
    note = monday_note(checkins=("- 09:40 — ajuste: bloque perdido · 10:00–09:00 · t-aaaaaa",))
    vault.write(MONDAY, note, expected=vault.read(MONDAY).version)
    clock = FakeClock(at(MON, "10:00"))
    tools = ToolRegistry(vault, samples.effective(), MemoryAuditLog(), clock)
    briefing = CoachSession(tools, clock).briefing()
    assert briefing.adjustments == ()
    assert briefing.rejected_adjustments == 1


def test_gap_in_the_revision_chain_is_an_error(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    clock = FakeClock(NOW)
    tools = ToolRegistry(vault, samples.effective(), MemoryAuditLog(), clock)
    jefe = JefePlanner(tools, clock)
    jefe.proponer(WEEK)
    jefe.aprobar(WEEK)
    write_note("Semanal/2026-W45-r2.md", vault.read(VaultPath.parse("Semanal/2026-W45.md")).text)
    clock.current = at(MON, "09:00")
    with pytest.raises(ApplicationError, match="r1"):
        jefe.replanificar(WEEK)


# --- code-reviewer ----------------------------------------------------------------------------


def _replanning_world(
    vault: FilesystemVault, write_note: WriteNote
) -> tuple[JefePlanner, CoachSession, FakeClock]:
    write_note("_sistema/horario.md", HORARIO)
    write_note(
        "01-Universidad/redes.md",
        "- [ ] Informe [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 6h]\n",
    )
    clock = FakeClock(NOW)
    tools = ToolRegistry(vault, samples.effective(), MemoryAuditLog(), clock)
    jefe = JefePlanner(tools, clock)
    jefe.proponer(WEEK)
    jefe.aprobar(WEEK)
    return jefe, CoachSession(tools, clock), clock


def test_reduce_load_still_applies_in_a_second_revision_the_same_day(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    jefe, coach, clock = _replanning_world(vault, write_note)
    clock.current = at(MON, "08:20")
    coach.checkin("", state=CheckInState.CANSADO)
    clock.current = at(MON, "08:25")
    jefe.replanificar(WEEK)
    jefe.aprobar(WEEK)
    clock.current = at(MON, "12:30")
    jefe.replanificar(WEEK)
    note = parse_weekly(vault.read(VaultPath.parse("Semanal/2026-W45-r2.md")).text)
    assert all(
        b.interval.start.astimezone(LIMA).date() != MON for b in note.plan_blocks() if b.task_id
    )


@pytest.mark.usefixtures("seeded")
def test_record_and_proposal_are_written_together(vault: FilesystemVault) -> None:
    audit = MemoryAuditLog()
    clock = FakeClock(at(MON, "09:45"))
    tools = ToolRegistry(vault, samples.effective(), audit, clock)
    CoachSession(tools, clock).checkin("", state=CheckInState.PERDIDO, task_id=tid("t-aaaaaa"))
    writes = [r for r in audit.records if r.action == "anexar_checkin" and r.result.value == "ok"]
    assert len(writes) == 1
    assert len(_checkins(vault)) == 2


@pytest.mark.usefixtures("seeded")
def test_duplicated_checkins_section_names_the_day(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    jefe, _, clock = _replanning_world_from_seeded(vault)
    tuesday = VaultPath.parse("Diario/2026-11-03.md")
    text = vault.read(tuesday).text + "\n## Check-ins\n"
    vault.write(tuesday, text, expected=vault.read(tuesday).version)
    clock.current = at(MON, "09:00")
    with pytest.raises(ApplicationError, match="2026-11-03"):
        jefe.replanificar(WEEK)


def _replanning_world_from_seeded(
    vault: FilesystemVault,
) -> tuple[JefePlanner, CoachSession, FakeClock]:
    clock = FakeClock(NOW)
    tools = ToolRegistry(vault, samples.effective(), MemoryAuditLog(), clock)
    jefe = JefePlanner(tools, clock)
    jefe.proponer(WEEK)
    jefe.aprobar(WEEK)
    return jefe, CoachSession(tools, clock), clock


def test_total_calls_is_the_sum_of_the_rows() -> None:
    scan = UsageScan(
        records=(record(correlation_id="c1"), record(correlation_id="c1", agent="jefe")),
        malformed=0,
        files=1,
    )
    summary = summarize(scan)
    assert summary.total.calls == sum(row.calls for row in summary.rows) == 2


def test_repeated_progress_for_one_task_is_refused_by_the_cli(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        ["cierre", "--avance", "t-aaaaaa=10", "--avance", "t-aaaaaa=50"]
    )
    with pytest.raises(ValidationError, match="t-aaaaaa"):
        _answers(args)
