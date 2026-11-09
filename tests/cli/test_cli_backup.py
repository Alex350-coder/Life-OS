"""`lifeos-respaldo` (SEC-14): la frase nunca se imprime y restaurar no toca la bóveda real."""

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lifeos.adapters.encrypted_backup import EncryptedVaultBackup
from lifeos.cli.backup import EXIT_OK, EXIT_REFUSED, main

pytestmark = pytest.mark.integration
PHRASE = "frase-de-prueba-larga"  # inventada
NOW = datetime(2026, 11, 9, 20, 0, tzinfo=UTC)


def _run(tmp_path: Path, *argv: str, answers: list[str], **env: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    pending = list(answers)
    code = main(
        list(argv),
        environ={"LIFEOS_LOGS_DIR": str(tmp_path / "logs"), **env},
        project_root=tmp_path / "project",
        out=out,
        err=err,
        ask=lambda prompt: pending.pop(0),
        backup=EncryptedVaultBackup(scrypt_n=2**10),
        now=NOW,
    )
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "boveda"
    root.mkdir()
    (root / "nota.md").write_text("hola", encoding="utf-8")
    (tmp_path / "project").mkdir()
    return root


def test_create_verify_and_restore_round_trip(vault: Path, tmp_path: Path) -> None:
    env = {"LIFEOS_VAULT_PATH": str(vault), "LIFEOS_BACKUP_DIR": str(tmp_path / "r")}
    code, out, _ = _run(tmp_path, "crear", answers=[PHRASE, PHRASE], **env)
    assert code == EXIT_OK and PHRASE not in out
    (archive,) = (tmp_path / "r").iterdir()
    code, out, _ = _run(tmp_path, "verificar", str(archive), answers=[PHRASE], **env)
    assert code == EXIT_OK and "1 archivos" in out
    target = tmp_path / "copia"
    code, _, _ = _run(
        tmp_path, "restaurar", str(archive), "--destino", str(target), answers=[PHRASE], **env
    )
    assert code == EXIT_OK
    assert (target / "nota.md").read_text(encoding="utf-8") == "hola"


def test_mismatched_phrases_create_nothing(vault: Path, tmp_path: Path) -> None:
    env = {"LIFEOS_VAULT_PATH": str(vault), "LIFEOS_BACKUP_DIR": str(tmp_path / "r")}
    code, _, err = _run(tmp_path, "crear", answers=[PHRASE, "otra-frase-larga-x"], **env)
    assert code == EXIT_REFUSED and "no coinciden" in err
    assert not (tmp_path / "r").exists()


def test_scheduled_backup_takes_the_phrase_from_the_environment(
    vault: Path, tmp_path: Path
) -> None:
    env = {
        "LIFEOS_VAULT_PATH": str(vault),
        "LIFEOS_BACKUP_DIR": str(tmp_path / "r"),
        "LIFEOS_BACKUP_PASSPHRASE": PHRASE,
    }
    code, out, err = _run(tmp_path, "crear", answers=[], **env)
    assert code == EXIT_OK and PHRASE not in out + err


def test_wrong_phrase_is_refused_without_echoing_it(vault: Path, tmp_path: Path) -> None:
    env = {"LIFEOS_VAULT_PATH": str(vault), "LIFEOS_BACKUP_DIR": str(tmp_path / "r")}
    _run(tmp_path, "crear", answers=[PHRASE, PHRASE], **env)
    (archive,) = (tmp_path / "r").iterdir()
    code, out, err = _run(tmp_path, "verificar", str(archive), answers=["equivocada-123"], **env)
    assert code == EXIT_REFUSED and "frase incorrecta" in err
    assert "equivocada" not in out + err
