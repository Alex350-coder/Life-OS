"""Fixtures comunes: bóveda temporal de prueba. Nunca se usa la bóveda real del propietario."""

from collections.abc import Callable
from pathlib import Path

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.ports.vault import VaultPath

WriteNote = Callable[[str, str], None]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture
def vault(root: Path) -> FilesystemVault:
    return FilesystemVault(root)


@pytest.fixture
def write_note(vault: FilesystemVault) -> WriteNote:
    def _write(path: str, text: str) -> None:
        vault.write(VaultPath.parse(path), text, expected=None)

    return _write


# Servidor HTTP local programado (tests/stub_http.py), compartido por adaptadores y SEC-08.
pytest_plugins = ["tests.stub_http"]
