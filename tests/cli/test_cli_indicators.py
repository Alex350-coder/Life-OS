"""`lifeos-indicadores` de punta a punta (F9-T03). Bóveda y logs inventados en `tmp_path`."""

import io
import shutil
from pathlib import Path

import pytest

from lifeos.cli.indicators import main
from tests.fakes import FakeClock
from tests.planning_builders import NOW
from tests.planning_samples import HORARIO

PROJECT = Path(__file__).resolve().parent.parent.parent
DUE = "\U0001f4c5"

pytestmark = pytest.mark.integration


def _run(root: Path, vault: Path | None, *argv: str) -> tuple[int, str, str]:
    environ = {"LIFEOS_LOGS_DIR": str(root / "logs"), "LIFEOS_TIMEZONE": "America/Lima"}
    if vault is not None:
        environ["LIFEOS_VAULT_PATH"] = str(vault)
    out, err = io.StringIO(), io.StringIO()
    code = main(
        list(argv), environ=environ, project_root=root, out=out, err=err, clock=FakeClock(NOW)
    )
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(PROJECT / "config", project / "config")
    return project


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    path = tmp_path / "vault"
    (path / "_sistema").mkdir(parents=True)
    (path / "_sistema" / "horario.md").write_text(HORARIO, encoding="utf-8")
    (path / "07-Cotidiano").mkdir()
    day = NOW.date().isoformat()
    (path / "07-Cotidiano" / "t.md").write_text(f"- [x] Pagar luz {DUE} {day}\n", encoding="utf-8")
    return path


def test_prints_every_indicator_and_the_unmeasured_ones(root: Path, vault: Path) -> None:
    code, out, err = _run(root, vault, "--dias", "7")
    assert (code, err) == (0, "")
    assert "Entregas cumplidas: 1 de 1 (100 %)" in out
    assert "Cumplimiento del plan diario: desconocido en 0 cierres" in out
    assert "sin llamadas a modelos" in out
    assert "Notificaciones: 0 enviadas" in out
    assert "Desviación entre esfuerzo estimado y real" in out


@pytest.mark.parametrize("days", ["0", "91", "x"])
def test_rejects_invalid_days(root: Path, vault: Path, days: str) -> None:
    code, _, err = _run(root, vault, "--dias", days)
    assert code == 2
    assert "Uso incorrecto" in err


def test_without_vault_is_a_configuration_error(root: Path) -> None:
    code, _, err = _run(root, None)
    assert code == 2
    assert "LIFEOS_VAULT_PATH" in err


def test_without_horario_is_refused(root: Path, vault: Path) -> None:
    (vault / "_sistema" / "horario.md").unlink()
    code, out, err = _run(root, vault)
    assert (code, out) == (1, "")
    assert "falta _sistema/horario.md" in err
