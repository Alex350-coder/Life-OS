"""CLI delgada (F3-T05, ADR-0006): `capturar`, `tareas`, `semana` y `aprobar` de punta a punta.

La CLI solo traduce argumentos y salida; las reglas viven en los casos de uso (ver
tests/test_architecture.py). Se usa una copia de `config/` y una bóveda temporal.
"""

import io
import json
import shutil
from pathlib import Path

import pytest
from lifeos.cli.main import main

from tests.fakes import FakeClock
from tests.planning_builders import NOW
from tests.planning_samples import HORARIO, HORARIO_SIN_LIMITES

PROJECT = Path(__file__).resolve().parent.parent.parent
TASKS = (
    "- [ ] Informe de redes [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 3h] 📅 2026-11-04\n"
    "- [ ] Leer capítulo [id:: t-bbbbbb] [area:: universidad] [esfuerzo:: 1h]\n"
    "- [x] Hecha [id:: t-cccccc] [area:: proyectos] [esfuerzo:: 1h]\n"
)

pytestmark = [pytest.mark.integration]


class Cli:
    def __init__(self, root: Path, vault: Path) -> None:
        self.root = root
        self.vault = vault
        self.environ = {
            "LIFEOS_VAULT_PATH": str(vault),
            "LIFEOS_LOGS_DIR": str(root / "logs"),
        }

    def run(self, *argv: str, **env: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            list(argv),
            environ={**self.environ, **env},
            project_root=self.root,
            clock=FakeClock(NOW),
            out=out,
            err=err,
        )
        return code, out.getvalue(), err.getvalue()

    def note(self, relative: str) -> str:
        return (self.vault / relative).read_text(encoding="utf-8")

    def write(self, relative: str, text: str) -> None:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    root = tmp_path / "project"
    shutil.copytree(PROJECT / "config", root / "config")
    vault = tmp_path / "vault"
    vault.mkdir()
    helper = Cli(root, vault)
    helper.write("_sistema/horario.md", HORARIO)
    helper.write("01-Universidad/redes.md", TASKS)
    return helper


# --- capturar y tareas ------------------------------------------------------------------------


def test_capturar_saves_an_inbox_note(cli: Cli) -> None:
    code, out, _ = cli.run("capturar", "Comprar", "cuaderno")
    assert code == 0
    (note,) = (cli.vault / "00-Inbox").glob("*.md")
    assert "Comprar cuaderno" in note.read_text(encoding="utf-8")
    assert "00-Inbox/" in out


def test_capturar_rejects_empty_text(cli: Cli) -> None:
    code, _, err = cli.run("capturar", "   ")
    assert code == 1
    assert "vacía" in err


def test_tareas_lists_open_tasks(cli: Cli) -> None:
    code, out, _ = cli.run("tareas")
    assert code == 0
    assert "t-aaaaaa" in out and "Informe de redes" in out and "2026-11-04" in out
    assert "t-cccccc" in out  # sin filtro de estado se listan todas


def test_tareas_filters(cli: Cli) -> None:
    _, out, _ = cli.run("tareas", "--estado", "completada")
    assert "t-cccccc" in out and "t-aaaaaa" not in out
    _, out, _ = cli.run("tareas", "--texto", "capítulo")
    assert "t-bbbbbb" in out and "t-aaaaaa" not in out


def test_tareas_invalid_filter_is_a_usage_error(cli: Cli) -> None:
    assert cli.run("tareas", "--area", "magia")[0] == 2


# --- semana y aprobar -------------------------------------------------------------------------


def test_semana_proposes_and_aprobar_publishes(cli: Cli) -> None:
    code, out, _ = cli.run("semana", "--semana", "2026-W45")
    assert code == 0
    assert "Semanal/2026-W45.md" in out
    assert "propuesto" in out
    assert "lifeos aprobar --semana 2026-W45" in out
    assert "estado_plan: propuesto" in cli.note("Semanal/2026-W45.md")

    code, out, _ = cli.run("aprobar", "--semana", "2026-W45")
    assert code == 0
    assert "aprobado" in out
    assert "estado_plan: aprobado" in cli.note("Semanal/2026-W45.md")
    assert "[id:: t-aaaaaa]" in cli.note("Diario/2026-11-02.md")


def test_semana_shows_unplanned_tasks_and_postponement(cli: Cli) -> None:
    cli.write(
        "02-Proyectos/grande.md",
        "- [ ] Proyecto enorme [id:: t-dddddd] [area:: proyectos] [esfuerzo:: 40h]\n",
    )
    _, out, _ = cli.run("semana", "--semana", "2026-W45")
    assert "No caben" in out
    assert "t-dddddd" in out and "2026-W46" in out


def test_rejected_plan_lists_conflicts_and_exits_1(cli: Cli) -> None:
    cli.run("semana", "--semana", "2026-W45")
    weekly = cli.note("Semanal/2026-W45.md").replace(
        "### mar 2026-11-03\n", "### mar 2026-11-03\n- 18:00–19:00 · Gym no [id:: t-bbbbbb]\n"
    )
    cli.write("Semanal/2026-W45.md", weekly)
    code, out, _ = cli.run("aprobar", "--semana", "2026-W45")
    assert code == 1
    assert "tiempo_protegido" in out
    assert "estado_plan: propuesto" in cli.note("Semanal/2026-W45.md")
    assert not (cli.vault / "Diario").exists()


def test_aprobar_without_proposal_fails(cli: Cli) -> None:
    code, _, err = cli.run("aprobar", "--semana", "2026-W45")
    assert code == 1
    assert "semana" in err


def test_missing_or_incomplete_schedule_is_explained(cli: Cli) -> None:
    (cli.vault / "_sistema/horario.md").unlink()
    code, _, err = cli.run("semana", "--semana", "2026-W45")
    assert code == 1 and "horario" in err
    cli.write("_sistema/horario.md", HORARIO_SIN_LIMITES)
    code, _, err = cli.run("semana", "--semana", "2026-W45")
    assert code == 1 and "D-13" in err


def test_invalid_week_is_a_usage_error(cli: Cli) -> None:
    assert cli.run("semana", "--semana", "2026-W99")[0] == 2


def test_timezone_must_match_the_schedule(cli: Cli) -> None:
    code, _, err = cli.run("semana", "--semana", "2026-W45", LIFEOS_TIMEZONE="Europe/Madrid")
    assert code == 1 and "LIFEOS_TIMEZONE" in err


def test_unknown_timezone_is_a_configuration_error(cli: Cli) -> None:
    code, _, err = cli.run("semana", LIFEOS_TIMEZONE="Marte/Olympus")
    assert code == 2 and "LIFEOS_TIMEZONE" in err


def test_missing_vault_path_is_a_configuration_error(cli: Cli) -> None:
    code, _, err = cli.run("tareas", LIFEOS_VAULT_PATH="")
    assert code == 2 and "LIFEOS_VAULT_PATH" in err


def test_ai_proposal_without_key_is_a_configuration_error(cli: Cli) -> None:
    code, _, err = cli.run("semana", "--semana", "2026-W45", "--ia")
    assert code == 2
    assert "NVIDIA_API_KEY" in err
    assert not (cli.vault / "Semanal").exists()


def test_publication_is_audited_in_the_logs(cli: Cli) -> None:
    cli.run("semana", "--semana", "2026-W45")
    (log,) = (cli.root / "logs").glob("audit-*.jsonl")
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert {(r["actor"], r["action"], r["result"]) for r in records} >= {
        ("jefe", "publicar_plan", "ok")
    }
    assert "Informe de redes" not in log.read_text(encoding="utf-8")  # sin contenido de notas


def test_help_lists_the_commands() -> None:
    out = io.StringIO()
    with pytest.raises(SystemExit) as raised:
        main(["--help"], out=out)
    assert raised.value.code == 0
