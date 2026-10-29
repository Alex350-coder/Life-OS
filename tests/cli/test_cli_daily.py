"""CLI del ciclo diario (F4-T05): `hoy`, `checkin`, `cierre`, `uso` y `semana --replanificar`.

Recorre planificar → ejecutar → registrar → replanificar de punta a punta sobre una copia de
`config/` y una bóveda temporal con datos inventados.
"""

import io
import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from lifeos.cli.main import main
from tests.daily_builders import TASKS
from tests.fakes import FakeClock
from tests.planning_builders import LIMA, MON, NOW, TUE, at
from tests.planning_samples import HORARIO

PROJECT = Path(__file__).resolve().parent.parent.parent
MONDAY = "Diario/2026-11-02.md"
FAKE_KEY = "nvapi-" + "x" * 20  # construida aquí para que el control de secretos no la vea

pytestmark = [pytest.mark.integration]


class DailyCli:
    def __init__(self, root: Path, vault: Path) -> None:
        self.root = root
        self.vault = vault
        self.environ = {"LIFEOS_VAULT_PATH": str(vault), "LIFEOS_LOGS_DIR": str(root / "logs")}

    def run(self, *argv: str, now: datetime = NOW, **env: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            list(argv),
            environ={**self.environ, **env},
            project_root=self.root,
            clock=FakeClock(now),
            out=out,
            err=err,
        )
        return code, out.getvalue(), err.getvalue()

    def note(self, relative: str) -> str:
        return (self.vault / relative).read_text(encoding="utf-8")

    def snapshot(self) -> dict[str, str]:
        return {
            p.relative_to(self.vault).as_posix(): p.read_text(encoding="utf-8")
            for p in self.vault.rglob("*.md")
        }


@pytest.fixture
def cli(tmp_path: Path) -> DailyCli:
    root = tmp_path / "project"
    shutil.copytree(PROJECT / "config", root / "config")
    vault = tmp_path / "vault"
    for relative, text in (("_sistema/horario.md", HORARIO), ("01-Universidad/redes.md", TASKS)):
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    helper = DailyCli(root, vault)
    assert helper.run("semana", "--semana", "2026-W45")[0] == 0
    assert helper.run("aprobar", "--semana", "2026-W45")[0] == 0
    return helper


def _first_monday_task(cli: DailyCli) -> str:
    line = next(
        line for line in cli.note(MONDAY).split("## Bloques", 1)[1].splitlines() if "[id::" in line
    )
    return line.split("[id:: ", 1)[1].rstrip("]")


# --- hoy --------------------------------------------------------------------------------------


def test_hoy_shows_the_day_and_the_current_block(cli: DailyCli) -> None:
    code, out, _ = cli.run("hoy", now=at(MON, "08:15"))
    assert code == 0
    assert "2026-11-02" in out
    assert "Ahora:" in out and _first_monday_task(cli) in out
    assert "Siguiente:" in out


def test_hoy_without_plan_for_today_explains_what_to_do(cli: DailyCli) -> None:
    code, _, err = cli.run("hoy", now=datetime(2026, 11, 10, 9, 0, tzinfo=LIMA))
    assert code == 1
    assert "aprobar" in err


def test_hoy_mentions_protected_time(cli: DailyCli) -> None:
    _, out, _ = cli.run("hoy", now=at(TUE, "18:30"))
    assert "Tiempo protegido" in out and "Gym" in out


# --- checkin ----------------------------------------------------------------------------------


def test_checkin_blocked_offers_a_micro_step(cli: DailyCli) -> None:
    code, out, _ = cli.run(
        "checkin", "No", "arranco", "--estado", "bloqueado", now=at(MON, "08:10")
    )
    assert code == 0
    assert "Micro-paso" in out and "min" in out
    assert "estado: bloqueado · nota: No arranco" in cli.note(MONDAY)


def test_checkin_missed_registers_a_proposal(cli: DailyCli) -> None:
    task = _first_monday_task(cli)
    code, out, _ = cli.run("checkin", "--estado", "perdido", "--tarea", task, now=at(MON, "11:00"))
    assert code == 0
    assert "--replanificar" in out
    assert "ajuste: bloque perdido" in cli.note(MONDAY)


def test_checkin_only_touches_the_checkins_section(cli: DailyCli) -> None:
    before = cli.snapshot()
    cli.run("checkin", "Hola", now=at(MON, "08:10"))
    after = cli.snapshot()
    changed = {k for k in after if after[k] != before.get(k)}
    assert changed == {MONDAY}
    assert after[MONDAY].split("## Check-ins")[0] == before[MONDAY].split("## Check-ins")[0]


def test_checkin_with_unknown_state_is_a_usage_error(cli: DailyCli) -> None:
    assert cli.run("checkin", "--estado", "feliz", now=at(MON, "08:10"))[0] == 2


def test_checkin_with_invalid_task_id_is_a_usage_error(cli: DailyCli) -> None:
    assert cli.run("checkin", "--tarea", "t-1", now=at(MON, "08:10"))[0] == 2


def test_checkin_with_ai_and_no_key_is_a_configuration_error(cli: DailyCli) -> None:
    before = cli.snapshot()
    code, _, err = cli.run("checkin", "Hola", "--ia", now=at(MON, "08:10"))
    assert code == 2 and "NVIDIA_API_KEY" in err
    assert cli.snapshot() == before


# --- cierre -----------------------------------------------------------------------------------


def test_cierre_without_confirmation_changes_nothing(cli: DailyCli) -> None:
    task = _first_monday_task(cli)
    before = cli.snapshot()
    code, out, _ = cli.run("cierre", "--avance", f"{task}=40", now=at(MON, "21:00"))
    assert code == 0
    assert "40%" in out
    assert "--confirmar" in out
    assert cli.snapshot() == before
    audit = "".join(p.read_text(encoding="utf-8") for p in (cli.root / "logs").glob("audit-*"))
    assert "propietario" not in audit


def test_cierre_confirmed_updates_the_task_and_the_closing(cli: DailyCli) -> None:
    task = _first_monday_task(cli)
    code, _, _ = cli.run(
        "cierre",
        "--avance",
        f"{task}=40",
        "--restante",
        f"{task}=1h",
        "--confirmar",
        now=at(MON, "21:00"),
    )
    assert code == 0
    tasks = cli.note("01-Universidad/redes.md")
    line = next(line for line in tasks.splitlines() if task in line)
    assert "[avance:: 40%]" in line and "[esfuerzo:: 1h]" in line
    assert f"avance: {task}" in cli.note(MONDAY).split("## Cierre", 1)[1]


def test_cierre_not_done_proposes_relocation(cli: DailyCli) -> None:
    task = _first_monday_task(cli)
    code, _, _ = cli.run("cierre", "--no", task, "--confirmar", now=at(MON, "21:00"))
    assert code == 0
    assert "ajuste: bloque perdido" in cli.note(MONDAY)


def test_cierre_confirm_without_answers_is_refused(cli: DailyCli) -> None:
    before = cli.snapshot()
    code, _, err = cli.run("cierre", "--confirmar", now=at(MON, "21:00"))
    assert code == 1 and "respuesta" in err
    assert cli.snapshot() == before


@pytest.mark.parametrize(
    "args",
    [("--avance", "t-aaaaaa=muchos"), ("--restante", "t-aaaaaa=0h"), ("--avance", "t-aaaaaa")],
)
def test_cierre_malformed_values_are_usage_errors(cli: DailyCli, args: tuple[str, str]) -> None:
    assert cli.run("cierre", *args, now=at(MON, "21:00"))[0] == 2


def test_cierre_done_with_progress_for_the_same_task_is_refused(cli: DailyCli) -> None:
    task = _first_monday_task(cli)
    code, _, _ = cli.run("cierre", "--hecho", task, "--avance", f"{task}=50", now=at(MON, "21:00"))
    assert code == 1


# --- semana --replanificar ----------------------------------------------------------------------


def test_replanificar_then_aprobar_publishes_a_revision(cli: DailyCli) -> None:
    task = _first_monday_task(cli)
    cli.run("checkin", "--estado", "perdido", "--tarea", task, now=at(MON, "11:00"))
    code, out, _ = cli.run("semana", "--semana", "2026-W45", "--replanificar", now=at(MON, "11:05"))
    assert code == 0
    assert "Semanal/2026-W45-r1.md" in out
    assert "lifeos aprobar --semana 2026-W45" in out
    code, out, _ = cli.run("aprobar", "--semana", "2026-W45", now=at(MON, "11:10"))
    assert code == 0
    assert "estado_plan: aprobado" in cli.note("Semanal/2026-W45-r1.md")


def test_semana_on_an_approved_week_points_to_replanning(cli: DailyCli) -> None:
    code, _, err = cli.run("semana", "--semana", "2026-W45", now=at(MON, "11:00"))
    assert code == 1 and "--replanificar" in err


# --- uso --------------------------------------------------------------------------------------


def test_uso_without_calls_says_so(cli: DailyCli) -> None:
    code, out, _ = cli.run("uso", "--mes", "2026-11")
    assert code == 0
    assert "Sin llamadas" in out


def test_uso_summarizes_logged_calls_without_content(cli: DailyCli) -> None:
    logs = cli.root / "logs"
    logs.mkdir(exist_ok=True)
    line = {
        "ts": "2026-11-02T14:00:00Z",
        "correlation_id": "c1",
        "provider": "nvidia",
        "model": "proveedor/modelo",
        "profile": "ligero",
        "agent": "coach",
        "ritual": "checkin",
        "attempt": 1,
        "latency_ms": 90,
        "tokens_prompt": 10,
        "tokens_completion": 4,
        "cost_usd": None,
        "cost_status": "desconocido",
        "outcome": "ok",
        "http_status": 200,
    }
    (logs / "usage-2026-11.jsonl").write_text(
        json.dumps(line) + f"\nlinea dañada con secreto {FAKE_KEY}\n",
        encoding="utf-8",
    )
    code, out, _ = cli.run("uso", "--mes", "2026-11")
    assert code == 0
    assert "coach" in out and "checkin" in out
    assert "desconocido" in out
    assert "1 línea" in out
    assert "nvapi" not in out


def test_uso_does_not_need_a_vault(cli: DailyCli) -> None:
    assert cli.run("uso", LIFEOS_VAULT_PATH="")[0] == 0


def test_uso_invalid_month_is_a_usage_error(cli: DailyCli) -> None:
    assert cli.run("uso", "--mes", "2026-13")[0] == 2
