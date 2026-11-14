"""Respaldo cifrado de la bóveda (SEC-14, ADR-0016): restauración probada en un entorno aparte."""

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lifeos.adapters.encrypted_backup import MAGIC, EncryptedVaultBackup
from lifeos.ports.backup import BackupError

pytestmark = [pytest.mark.integration, pytest.mark.security]
NOW = datetime(2026, 11, 9, 20, 0, tzinfo=UTC)
PHRASE = "frase-de-prueba-larga"  # inventada; nunca es una frase real


@pytest.fixture
def backup() -> EncryptedVaultBackup:
    return EncryptedVaultBackup(scrypt_n=2**10)  # rápido en pruebas; el real usa 2**17


@pytest.fixture
def source(tmp_path: Path) -> Path:
    vault = tmp_path / "boveda"
    (vault / "_sistema").mkdir(parents=True)
    (vault / "_sistema" / "horario.md").write_text("# Horario\nlun 08:00–24:00\n", encoding="utf-8")
    (vault / "01-Universidad").mkdir()
    (vault / "01-Universidad" / "redes.md").write_text("- [ ] Tarea ñ\n", encoding="utf-8")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    (vault / ".trash").mkdir()
    (vault / ".trash" / "vieja.md").write_text("borrada", encoding="utf-8")
    (vault / ".lifeos-temporal.md").write_text("a medio escribir", encoding="utf-8")
    return vault


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_backup_restores_byte_for_byte_in_a_separate_folder(
    backup: EncryptedVaultBackup, source: Path, tmp_path: Path
) -> None:
    created = backup.create(source, tmp_path / "respaldos", PHRASE, NOW)
    assert created.path.name == "lifeos-20261109-200000.lorespaldo"
    restored = backup.restore(created.path, PHRASE, tmp_path / "restaurada")
    expected = {k: v for k, v in _tree(source).items() if not k.startswith((".trash", ".lifeos-"))}
    assert _tree(tmp_path / "restaurada") == expected
    assert restored.files == created.files == 3


def test_the_archive_is_encrypted(
    backup: EncryptedVaultBackup, source: Path, tmp_path: Path
) -> None:
    data = backup.create(source, tmp_path / "r", PHRASE, NOW).path.read_bytes()
    assert data.startswith(MAGIC)
    assert b"horario" not in data and b"Tarea" not in data


def test_wrong_passphrase_and_tampering_are_detected(
    backup: EncryptedVaultBackup, source: Path, tmp_path: Path
) -> None:
    path = backup.create(source, tmp_path / "r", PHRASE, NOW).path
    with pytest.raises(BackupError, match="frase incorrecta"):
        backup.verify(path, "otra-frase-larga-123")
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(bytes(data))
    with pytest.raises(BackupError, match="frase incorrecta o respaldo dañado"):
        backup.verify(path, PHRASE)


def test_verify_counts_without_writing(
    backup: EncryptedVaultBackup, source: Path, tmp_path: Path
) -> None:
    path = backup.create(source, tmp_path / "r", PHRASE, NOW).path
    info = backup.verify(path, PHRASE)
    assert info.files == 3
    assert sorted(p.name for p in (tmp_path / "r").iterdir()) == [path.name]


def test_restore_never_overwrites(
    backup: EncryptedVaultBackup, source: Path, tmp_path: Path
) -> None:
    path = backup.create(source, tmp_path / "r", PHRASE, NOW).path
    with pytest.raises(BackupError, match="vacío"):
        backup.restore(path, PHRASE, source)


@pytest.mark.parametrize("phrase", ["", "corta"])
def test_short_passphrases_are_refused(
    backup: EncryptedVaultBackup, source: Path, tmp_path: Path, phrase: str
) -> None:
    with pytest.raises(BackupError, match="12 caracteres"):
        backup.create(source, tmp_path / "r", phrase, NOW)


def test_target_inside_the_vault_is_refused(backup: EncryptedVaultBackup, source: Path) -> None:
    with pytest.raises(BackupError, match="dentro de la bóveda"):
        backup.create(source, source / "respaldos", PHRASE, NOW)


def test_an_existing_backup_is_never_overwritten(
    backup: EncryptedVaultBackup, source: Path, tmp_path: Path
) -> None:
    backup.create(source, tmp_path / "r", PHRASE, NOW)
    with pytest.raises(BackupError, match="ya existe"):
        backup.create(source, tmp_path / "r", PHRASE, NOW)


@pytest.mark.parametrize("name", ["../fuera.md", "/abs.md", "C:/x.md"])
def test_zip_slip_paths_are_refused(
    backup: EncryptedVaultBackup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    # Un respaldo manipulado (con la frase correcta) no puede escribir fuera del destino.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, "x")
    monkeypatch.setattr(
        EncryptedVaultBackup, "_zip", staticmethod(lambda source: (buffer.getvalue(), 1, 1))
    )
    empty = tmp_path / "vacia"
    empty.mkdir()
    path = backup.create(empty, tmp_path / "r", PHRASE, NOW).path
    with pytest.raises(BackupError, match="ruta no válida"):
        backup.restore(path, PHRASE, tmp_path / "destino")
    assert not (tmp_path / "fuera.md").exists()


def test_not_a_backup_is_reported(backup: EncryptedVaultBackup, tmp_path: Path) -> None:
    other = tmp_path / "x.lorespaldo"
    other.write_bytes(b"hola")
    with pytest.raises(BackupError, match="no es un respaldo"):
        backup.verify(other, PHRASE)
