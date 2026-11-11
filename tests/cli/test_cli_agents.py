"""`lifeos-agentes` de punta a punta (F8-T01/T02): ciclo de vida, especialista y revisión.

Bóveda inventada; sin modelo (heurística). Comprueba los criterios de la Fase 8: registrar sin
tocar el código, rechazar con un error útil, no activar al importar, desactivar impide ejecutar,
restaurar recupera la versión previa y el especialista no escribe el plan.
"""

import io
import json
import shutil
from pathlib import Path

import pytest

from lifeos.cli.agents import main
from tests.fakes import FakeClock
from tests.planning_builders import NOW

PROJECT = Path(__file__).resolve().parent.parent.parent
DUE = "\U0001f4c5"
TASKS = f"- [ ] Informe [id:: t-abc234] [area:: universidad] [esfuerzo:: 2h] {DUE} 2026-11-03\n"
GOALS = "- [meta:: m1] Aprobar Redes [horizonte:: 2026-12-15]\n"

pytestmark = pytest.mark.integration


class Agents:
    def __init__(self, root: Path, vault: Path) -> None:
        self.root = root
        self.vault = vault
        self.environ = {
            "LIFEOS_VAULT_PATH": str(vault),
            "LIFEOS_LOGS_DIR": str(root / "logs"),
            "LIFEOS_STATE_DIR": str(root / "state"),
            "LIFEOS_TIMEZONE": "America/Lima",
        }

    def run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            list(argv),
            environ=self.environ,
            project_root=self.root,
            out=out,
            err=err,
            clock=FakeClock(NOW),
        )
        return code, out.getvalue(), err.getvalue()

    def template(self, name: str = "universidad") -> str:
        return str(self.root / "config" / "especialistas" / f"{name}.yaml")


@pytest.fixture
def agents(tmp_path: Path) -> Agents:
    root = tmp_path / "project"
    shutil.copytree(PROJECT / "config", root / "config")
    vault = tmp_path / "vault"
    (vault / "01-Universidad").mkdir(parents=True)
    (vault / "01-Universidad" / "redes.md").write_text(TASKS, encoding="utf-8")
    (vault / "01-Universidad" / "metas.md").write_text(GOALS, encoding="utf-8")
    return Agents(root, vault)


def _activate(agents: Agents) -> None:
    for argv in (
        ("importar", agents.template()),
        ("probar", "universidad"),
        ("aprobar", "universidad"),
        ("activar", "universidad"),
    ):
        code, out, err = agents.run(*argv)
        assert code == 0, (argv, out, err)


def test_import_registers_draft_and_shows_effective_permissions(agents: Agents) -> None:
    code, out, _ = agents.run("importar", agents.template())

    assert code == 0
    assert "universidad: v1 en borrador" in out
    assert "lectura: 01-Universidad/" in out
    assert "escritura: Propuestas/ (crear)" in out
    assert "No se activa" in out
    _, listing, _ = agents.run("lista")
    assert "jefe · núcleo · activo" in listing
    assert "universidad · v1 · borrador" in listing


def test_full_cycle_runs_and_owner_accepts(agents: Agents) -> None:
    _activate(agents)

    code, out, _ = agents.run("ejecutar", "universidad", "--semana", "2026-W45")
    assert code == 0, out
    assert "Propuestas/2026-W45/universidad.md" in out
    assert not (agents.vault / "Semanal").exists()  # el especialista no toca el plan

    code, out, _ = agents.run("propuestas", "--semana", "2026-W45")
    assert code == 0
    assert "2026-W45/universidad/p1 · prioridad · Informe: media → alta" in out

    code, out, _ = agents.run("aceptar", "2026-W45/universidad/p1")
    assert code == 0, out
    tasks = (agents.vault / "01-Universidad" / "redes.md").read_text(encoding="utf-8")
    assert "[prioridad:: alta]" in tasks

    code, out, _ = agents.run("rechazar", "2026-W45/universidad/p2")
    assert code == 0
    _, out, _ = agents.run("propuestas", "--semana", "2026-W45")
    assert "No hay propuestas pendientes" in out


def test_import_never_activates(agents: Agents) -> None:
    agents.run("importar", agents.template())

    code, _, err = agents.run("ejecutar", "universidad")

    assert code == 1
    assert "borrador" in err


def test_malformed_and_excessive_configs_are_rejected_with_useful_errors(
    agents: Agents, tmp_path: Path
) -> None:
    broken = tmp_path / "roto.yaml"
    broken.write_text("id: [roto\n", encoding="utf-8")
    greedy = tmp_path / "codicioso.yaml"
    text = Path(agents.template()).read_text(encoding="utf-8")
    greedy.write_text(
        text.replace("    - 01-Universidad/\n", "    - 01-Universidad/\n    - 05-Salud/\n"),
        encoding="utf-8",
    )

    code, _, err = agents.run("importar", str(broken))
    assert code == 1 and "YAML no válido" in err
    code, _, err = agents.run("importar", str(greedy))
    assert code == 1 and "05-Salud" in err and "restringida" in err
    _, listing, _ = agents.run("lista")
    assert "universidad" not in listing


def test_deactivate_blocks_runs_and_restore_recovers_previous_version(
    agents: Agents, tmp_path: Path
) -> None:
    _activate(agents)
    v2 = tmp_path / "universidad-v2.yaml"
    text = Path(agents.template()).read_text(encoding="utf-8")
    v2.write_text(
        text.replace("\nversion: 1", "\nversion: 2").replace("Revisar", "Vigilar"),
        encoding="utf-8",
    )

    code, out, err = agents.run("importar", str(v2))
    assert code == 0, err  # v2 en borrador: deja de ejecutarse
    assert agents.run("ejecutar", "universidad")[0] == 1
    code, out, _ = agents.run("restaurar", "universidad", "--version", "1")
    assert code == 0 and "v1" in out and "aprobado" in out
    assert agents.run("activar", "universidad")[0] == 0
    _, exported, _ = agents.run("exportar", "universidad")
    assert exported == text

    assert agents.run("desactivar", "universidad")[0] == 0
    code, _, err = agents.run("ejecutar", "universidad")
    assert code == 1 and "desactivado" in err


def test_audit_records_lifecycle_without_file_content(agents: Agents) -> None:
    _activate(agents)

    lines = []
    for path in sorted((agents.root / "logs").glob("audit-*.jsonl")):
        lines += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    actions = [(r["action"], r["result"]) for r in lines if r["actor"] == "propietario"]
    assert ("agente_importar", "ok") in actions
    assert ("agente_activar", "ok") in actions
    assert all("Revisar" not in json.dumps(r) for r in lines)


def test_export_to_file_refuses_overwrite(agents: Agents, tmp_path: Path) -> None:
    agents.run("importar", agents.template())
    target = tmp_path / "copia.yaml"
    target.write_text("ya existe", encoding="utf-8")

    code, _, err = agents.run("exportar", "universidad", "--salida", str(target))

    assert code == 1 and "ya existe" in err
    assert target.read_text(encoding="utf-8") == "ya existe"


def test_usage_errors(agents: Agents) -> None:
    assert agents.run("restaurar", "universidad")[0] == 2
    assert agents.run("aceptar", "no-es-ref")[0] == 2
    assert agents.run("importar", str(agents.root / "no-existe.yaml"))[0] == 1
