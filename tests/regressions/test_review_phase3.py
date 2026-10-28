"""Regresiones de la revisión de la Fase 3 (code, security, python, silent-failure y agent
evaluator). Cada prueba reproduce un hallazgo antes de corregirlo."""

import io
import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.schemas import SCHEMAS
from lifeos.adapters.yaml_config import parse_catalog
from lifeos.application.jefe import JefePlanner
from lifeos.application.jefe_ai import AiPlanDrafter, build_messages
from lifeos.application.llm_calls import ModelGateway
from lifeos.application.plan_publication import PartialPublicationError
from lifeos.application.policy_vault import PermissionDeniedError, PolicyVault
from lifeos.application.tasks import ApplicationError, TaskQuery
from lifeos.application.tools import ToolRegistry
from lifeos.cli.main import main
from lifeos.domain.note import parse_note
from lifeos.domain.plan import ConflictCode, PlanOrigin, PlanStatus
from lifeos.domain.plan_note import parse_weekly
from lifeos.ports.config import WriteMode
from lifeos.ports.llm import ModelOutputError
from lifeos.ports.vault import VaultIOError, VaultPath, Version
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.fakes import FakeClock, FakeLlmClient, MemoryAuditLog, MemoryUsageLog, ok_response
from tests.planning_builders import NOW, WEEK
from tests.planning_samples import HORARIO, horario

pytestmark = [pytest.mark.integration, pytest.mark.security]

WEEKLY = VaultPath.parse("Semanal/2026-W45.md")
MONDAY = VaultPath.parse("Diario/2026-11-02.md")
TASKS = "- [ ] Informe [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 2h]\n"
# Dígitos de ancho completo (U+FF10…): \d los acepta sin re.ASCII.
FULLWIDTH_2026 = "".join(chr(0xFF10 + int(d)) for d in "2026")
PROJECT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def audit() -> MemoryAuditLog:
    return MemoryAuditLog()


@pytest.fixture
def tools(vault: FilesystemVault, audit: MemoryAuditLog, write_note: WriteNote) -> ToolRegistry:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    return ToolRegistry(vault, samples.effective(), audit, FakeClock(NOW))


@pytest.fixture
def jefe(tools: ToolRegistry) -> JefePlanner:
    return JefePlanner(tools, FakeClock(NOW))


def _edit(vault: FilesystemVault, path: VaultPath, change: object) -> None:
    snapshot = vault.read(path)
    vault.write(path, change(snapshot.text), expected=snapshot.version)  # type: ignore[operator]


# --- Security HIGH: se publicaba la nota editada a mano tal cual -------------------------------


def test_approval_publishes_a_canonical_note_without_smuggled_tasks(
    jefe: JefePlanner, tools: ToolRegistry, vault: FilesystemVault
) -> None:
    jefe.proponer(WEEK)
    _edit(
        vault,
        WEEKLY,
        lambda text: text + "\n## Notas\n- [ ] Pagar X [id:: t-abcdef] [area:: finanzas]\n",
    )
    assert jefe.aprobar(WEEK).accepted
    published = vault.read(WEEKLY).text
    assert parse_note(published).tasks == ()
    ids = {str(t.task.id) for t in tools.listar_tareas(jefe.new_actor(), TaskQuery())}
    assert "t-abcdef" not in ids


def test_duplicate_plan_section_fails_closed(jefe: JefePlanner, vault: FilesystemVault) -> None:
    jefe.proponer(WEEK)
    _edit(
        vault,
        WEEKLY,
        lambda text: (
            text + "\n## Bloques por día\n### lun 2026-11-02\n- 15:00–16:00 · X [id:: t-aaaaaa]\n"
        ),
    )
    with pytest.raises(ApplicationError):
        jefe.aprobar(WEEK)
    assert not vault.exists(MONDAY)


def test_crlf_weekly_note_can_be_approved(jefe: JefePlanner, vault: FilesystemVault) -> None:
    jefe.proponer(WEEK)
    _edit(vault, WEEKLY, lambda text: text.replace("\n", "\r\n"))
    assert jefe.aprobar(WEEK).accepted
    assert parse_weekly(vault.read(WEEKLY).text).status is PlanStatus.APROBADO


def test_republishing_a_daily_note_keeps_the_owners_own_sections(
    jefe: JefePlanner, vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note(
        "Diario/2026-11-02.md",
        "---\ntipo: diario\n---\n\n## Notas personales\nNo olvidar llamar.\n\n## Check-ins\n",
    )
    jefe.proponer(WEEK)
    jefe.aprobar(WEEK)
    assert "## Notas personales\nNo olvidar llamar." in vault.read(MONDAY).text


def test_plan_note_names_reject_non_ascii_digits(vault: FilesystemVault) -> None:
    plan = PolicyVault(vault, samples.effective()["jefe"], WriteMode.PLAN)
    with pytest.raises(PermissionDeniedError) as info:
        plan.write(VaultPath.parse("Semanal/" + FULLWIDTH_2026 + "-W45.md"), "x\n", expected=None)
    assert info.value.reason == "nombre_de_plan_invalido"


# --- Silent HIGH: títulos saneados que impedían aprobar -----------------------------------------


def test_titles_with_brackets_can_be_proposed_and_approved(
    vault: FilesystemVault, audit: MemoryAuditLog, write_note: WriteNote
) -> None:
    write_note("_sistema/horario.md", horario(fijos="- lun 10:00–12:00 · Reunión [equipo]   A"))
    write_note("01-Universidad/redes.md", TASKS.replace("Informe", "Informe [v2]"))
    jefe = JefePlanner(
        ToolRegistry(vault, samples.effective(), audit, FakeClock(NOW)), FakeClock(NOW)
    )
    assert jefe.proponer(WEEK).accepted
    assert jefe.aprobar(WEEK).accepted


# --- Silent MEDIUM / security MEDIUM: aprobar valida con la hora real ---------------------------


def test_stale_plan_with_past_blocks_must_be_proposed_again(
    vault: FilesystemVault, audit: MemoryAuditLog, write_note: WriteNote
) -> None:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    tools = ToolRegistry(vault, samples.effective(), audit, FakeClock(NOW))
    JefePlanner(tools, FakeClock(NOW)).proponer(WEEK)
    later = FakeClock(NOW + timedelta(days=2))  # martes: el bloque del lunes ya pasó
    outcome = JefePlanner(tools, later).aprobar(WEEK)
    assert not outcome.accepted
    assert ConflictCode.FECHA_PASADA in {c.code for c in outcome.conflicts}
    assert not vault.exists(MONDAY)


def test_backdated_generation_time_does_not_relax_validation(
    vault: FilesystemVault, audit: MemoryAuditLog, write_note: WriteNote
) -> None:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    tools = ToolRegistry(vault, samples.effective(), audit, FakeClock(NOW + timedelta(days=2)))
    jefe = JefePlanner(tools, FakeClock(NOW + timedelta(days=2)))
    jefe.proponer(WEEK)
    _edit(
        vault,
        WEEKLY,
        lambda text: text.replace(
            "### lun 2026-11-02\n", "### lun 2026-11-02\n- 08:00–09:00 · Pasado [id:: t-aaaaaa]\n"
        ).replace("generado: 2026-11-03", "generado: 2026-10-01"),
    )
    outcome = jefe.aprobar(WEEK)
    assert ConflictCode.FECHA_PASADA in {c.code for c in outcome.conflicts}


# --- Silent HIGH: publicación a medias ----------------------------------------------------------


class FailingWeeklyVault(FilesystemVault):
    """Bóveda de prueba que falla al escribir la nota semanal (p. ej., OneDrive bloqueándola)."""

    def write(self, path: VaultPath, text: str, *, expected: Version | None) -> Version:
        if path.parent == VaultPath("Semanal") and "aprobado" in text:
            raise VaultIOError("archivo bloqueado")
        return super().write(path, text, expected=expected)


def test_partial_publication_is_reported_with_what_was_written(
    root: Path, audit: MemoryAuditLog
) -> None:
    vault = FailingWeeklyVault(root)
    vault.write(VaultPath.parse("_sistema/horario.md"), HORARIO, expected=None)
    vault.write(VaultPath.parse("01-Universidad/redes.md"), TASKS, expected=None)
    jefe = JefePlanner(
        ToolRegistry(vault, samples.effective(), audit, FakeClock(NOW)), FakeClock(NOW)
    )
    jefe.proponer(WEEK)
    with pytest.raises(PartialPublicationError) as raised:
        jefe.aprobar(WEEK)
    assert VaultPath.parse("Diario/2026-11-02.md") in raised.value.written


# --- Ruta IA (agent-evaluator) ------------------------------------------------------------------


def _ai(
    tools: ToolRegistry, usage: MemoryUsageLog, *texts: str, finish: str = "stop"
) -> JefePlanner:
    clock = FakeClock(NOW)
    client = FakeLlmClient(
        script=[ok_response(t, finish_reason=finish) for t in texts], clock=clock
    )
    gateway = ModelGateway(
        {"nvidia": client}, parse_catalog(samples.dump(samples.catalog()).encode()), clock, usage
    )
    drafter = AiPlanDrafter(gateway, SCHEMAS["plan_v1"], profile="grande")
    return JefePlanner(tools, clock, drafter=drafter)


def _plan(fecha: str, inicio: str = "08:00", fin: str = "09:00") -> str:
    block = {"tarea": "t-aaaaaa", "fecha": fecha, "inicio": inicio, "fin": fin}
    return json.dumps({"bloques": [block], "justificacion": "x"})


def test_extreme_model_date_is_an_invalid_output_not_a_crash(
    tools: ToolRegistry, vault: FilesystemVault
) -> None:
    jefe = _ai(tools, MemoryUsageLog(), _plan("9999-12-31", "23:00", "24:00"))
    with pytest.raises(ModelOutputError):
        jefe.proponer(WEEK, origin=PlanOrigin.IA)
    assert not vault.exists(WEEKLY)


def test_truncated_plan_output_saves_nothing(tools: ToolRegistry, vault: FilesystemVault) -> None:
    usage = MemoryUsageLog()
    jefe = _ai(tools, usage, _plan("2026-11-02"), finish="length")
    with pytest.raises(ModelOutputError):
        jefe.proponer(WEEK, origin=PlanOrigin.IA)
    assert not vault.exists(WEEKLY)
    assert [r.outcome for r in usage.records] == ["salida_invalida"]


def test_usage_and_audit_share_the_correlation_id(
    tools: ToolRegistry, audit: MemoryAuditLog
) -> None:
    usage = MemoryUsageLog()
    _ai(tools, usage, _plan("2026-11-02")).proponer(WEEK, origin=PlanOrigin.IA)
    published = [r for r in audit.records if r.action == "publicar_plan"]
    assert {r.correlation_id for r in published} == {usage.records[0].correlation_id}


def test_prompt_states_validator_rules_and_bounds_titles(
    tools: ToolRegistry, write_note: WriteNote
) -> None:
    write_note(
        "01-Universidad/x.md",  # el Jefe de las muestras lee 01-Universidad/
        "- [x] Hecha [id:: t-cccccc] [area:: proyectos] [esfuerzo:: 1h]\n"
        f"- [ ] {'L' * 500} [id:: t-dddddd] [area:: proyectos] [esfuerzo:: 1h]"
        " [depende:: t-cccccc]\n",
    )
    system, user = build_messages(JefePlanner(tools, FakeClock(NOW)).context(WEEK))
    for rule in ("no divisible", "dependencia", "bloque mínimo"):
        assert rule in system.content
    data = json.loads(user.content.split("\n", 1)[1])
    long_task = next(t for t in data["tareas"] if t["id"] == "t-dddddd")
    assert len(long_task["titulo"]) <= 120
    assert long_task["depende"] == []  # la dependencia ya está completada
    assert data["ahora"].startswith("2026-11-01T20:00")


def test_model_block_in_the_past_is_rejected(
    vault: FilesystemVault, audit: MemoryAuditLog, write_note: WriteNote
) -> None:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    later = NOW + timedelta(days=1, hours=16)  # lunes 12:00
    tools = ToolRegistry(vault, samples.effective(), audit, FakeClock(later))
    clock = FakeClock(later)
    client = FakeLlmClient(script=[ok_response(_plan("2026-11-02"))], clock=clock)
    gateway = ModelGateway(
        {"nvidia": client},
        parse_catalog(samples.dump(samples.catalog()).encode()),
        clock,
        MemoryUsageLog(),
    )
    jefe = JefePlanner(
        tools, clock, drafter=AiPlanDrafter(gateway, SCHEMAS["plan_v1"], profile="grande")
    )
    outcome = jefe.proponer(WEEK, origin=PlanOrigin.IA)
    assert ConflictCode.FECHA_PASADA in {c.code for c in outcome.conflicts}


# --- CLI (python HIGH, security LOW) -----------------------------------------------------------


def _cli(
    tmp_path: Path,
    vault: Path,
    *argv: str,
    without_jefe: bool = False,
    dotenv: str | None = None,
) -> tuple[int, str, str]:
    root = tmp_path / "project"
    shutil.copytree(PROJECT / "config", root / "config")
    if without_jefe:  # política válida, pero sin archivo de agente del Jefe
        (root / "config" / "agents" / "jefe.yaml").unlink()
    if dotenv is not None:
        (root / ".env").write_text(dotenv, encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    code = main(
        list(argv),
        environ={"LIFEOS_VAULT_PATH": str(vault), "LIFEOS_LOGS_DIR": str(tmp_path / "logs")},
        project_root=root,
        clock=FakeClock(NOW),
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


def test_cli_without_a_jefe_is_a_configuration_error(tmp_path: Path, root: Path) -> None:
    code, _, err = _cli(tmp_path, root, "semana", without_jefe=True)
    assert code == 2
    assert "Jefe" in err


def test_cli_malformed_dotenv_is_a_configuration_error(tmp_path: Path, root: Path) -> None:
    code, _, err = _cli(tmp_path, root, "tareas", dotenv="esto no es CLAVE=valor\n")
    assert code == 2
    assert ".env" in err


def test_cli_strips_terminal_control_characters(tmp_path: Path, root: Path) -> None:
    (root / "01-Universidad").mkdir()
    (root / "01-Universidad" / "x.md").write_text(
        "- [ ] Rojo \x1b[31mpeligro [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 1h]\n",
        encoding="utf-8",
    )
    code, out, _ = _cli(tmp_path, root, "tareas")
    assert code == 0
    assert "\x1b" not in out and "peligro" in out


# --- Code review HIGH: proponer a mitad de un hueco con segundos ------------------------------


def test_proposal_in_the_middle_of_the_week_with_seconds_is_minute_aligned(
    vault: FilesystemVault, audit: MemoryAuditLog, write_note: WriteNote
) -> None:
    write_note("_sistema/horario.md", HORARIO)
    write_note("01-Universidad/redes.md", TASKS)
    mid = NOW + timedelta(hours=12, minutes=15, seconds=27, microseconds=500_000)  # lun 08:15:27.5
    tools = ToolRegistry(vault, samples.effective(), audit, FakeClock(mid))
    jefe = JefePlanner(tools, FakeClock(mid))
    outcome = jefe.proponer(WEEK)
    assert outcome.accepted
    first = min(b.interval.start for b in outcome.proposal.work_blocks())
    assert (first.second, first.microsecond, first.minute) == (0, 0, 16)
    assert jefe.aprobar(WEEK).accepted
