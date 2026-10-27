"""Fixtures comunes: bóveda temporal de prueba. Nunca se usa la bóveda real del propietario."""

from pathlib import Path

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault


@pytest.fixture
def root(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture
def vault(root: Path) -> FilesystemVault:
    return FilesystemVault(root)


# Servidor HTTP local programado (tests/stub_http.py), compartido por adaptadores y SEC-08.
pytest_plugins = ["tests.stub_http"]
